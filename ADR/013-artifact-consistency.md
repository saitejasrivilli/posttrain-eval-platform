# ADR 013: Artifact Consistency Model — PostgreSQL and Object Storage Are Not One Transaction

## Status
Proposed -- pending user review before implementation. This is the central design problem of V0.5.

## Context
PostgreSQL and MinIO/S3 cannot commit atomically together -- there is no distributed transaction spanning both systems, and this project has never pretended otherwise for any two systems (Postgres+Kafka in V0.2 was solved with an outbox, not a fake distributed transaction). The two failure orderings explicitly called out:
```
metadata committed          bytes uploaded
       |                          |
       X (upload fails/crashes)   X (metadata write fails/crashes)
```
must both leave the system in a state that is *detectable and recoverable*, never silently inconsistent (a `ModelVersion` pointing at bytes that don't exist, or bytes sitting in storage forever with nothing knowing they exist).

## Decision: artifact lifecycle state machine, metadata-first, content-hash-verified reconciliation
```
(artifact row created)
        |
        v
     PENDING  --- upload succeeds, hash verified --->  UPLOADED
        |
        | (stuck past a grace period, OR upload confirmed failed)
        v
     FAILED
```

**Ordering: metadata row is written FIRST, in `PENDING` state, before any upload begins.** The row records the artifact's *intended* content hash and storage key (both computable before the upload, since identity is content-derived -- ADR 011) and `status=PENDING`. Only after the upload completes does a second, idempotent, conditional UPDATE (`WHERE status='PENDING'`, same single-conditional-UPDATE primitive as every prior version's fencing/reservation logic) flip it to `UPLOADED`.

**Required clarification: upload ownership via an upload lease (reuses ADR 004's mechanism, not a new pattern).** A `PENDING` row alone does not tell the Reconciler whether an uploader is still actively working on it -- without an ownership signal, this race is real:
```
T0: metadata PENDING
T1: Reconciler checks -- no bytes yet
T2: uploader is about to start (or is mid-upload)
T3: Reconciler marks FAILED
T4: uploader finishes uploading -- now FAILED + valid bytes, inconsistent
```
The fix is exactly V0.3's lease/fencing mechanism, applied to uploads instead of job execution: an uploader claims the artifact row atomically (`uploader_id`, `upload_lease_expires_at = now() + UPLOAD_LEASE_DURATION_SECONDS`, conditioned on `status='PENDING' AND (upload_lease_expires_at IS NULL OR upload_lease_expires_at < now())` -- same "claim" shape as `jobs.claim()`), and renews the lease periodically while streaming the upload (same heartbeat-on-its-own-timer requirement ADR 004 established for workers -- a large upload must not let a slow network stall block lease renewal). The Reconciler's rule for "is this `PENDING` row abandoned" becomes precise:
> **A `PENDING` artifact is eligible for Reconciler action only when `upload_lease_expires_at` is NULL (never claimed) and `created_at` is past the grace period, OR `upload_lease_expires_at` is in the past (claimed, but the lease lapsed -- the uploader died or stalled).** A `PENDING` row with a still-valid, unexpired lease is never touched by the Reconciler, full stop -- this is the same non-negotiable rule ADR 004 established for job leases, applied here.

This closes the race precisely: at T1 in the timeline above, if the uploader has claimed the lease before T1 (or claims it and starts renewing before the lease would be considered expired), the Reconciler's precondition simply doesn't match, and it moves on -- no ambiguity, no distributed lock, the same single-conditional-UPDATE primitive doing the work it's done in every prior version.

This ordering means:
- **Metadata commits, then upload fails or the process crashes:** the row exists, `status=PENDING`, forever, unless something intervenes. This is the *fully detectable* case -- nothing else in the system will ever treat this artifact as usable (the hard invariant: a `ModelVersion`/`DatasetVersion` may only reference `status=UPLOADED` artifacts), so a stuck `PENDING` row cannot silently corrupt anything downstream. A **Reconciler** process (new, same architectural role as V0.2's Outbox Relay / V0.3's Recovery / V0.4's Scheduler -- a poller, crash-tolerant by construction) periodically scans `PENDING` rows older than a grace period and, for each: checks object storage for an object at the row's (predetermined, content-derived) storage key. If present and its hash matches, the upload actually succeeded and just never got its status flip recorded -- self-heal to `UPLOADED`. If absent, the upload genuinely never completed -- mark `FAILED`.
- **Upload succeeds, but the status-flip UPDATE fails or crashes:** the row is stuck `PENDING` with bytes *actually present* at the key. This is exactly the self-healing case above -- the Reconciler's object-storage check finds the bytes, verifies the hash, and promotes the row to `UPLOADED`. Content-addressing (ADR 011) is what makes this self-heal safe: there's no ambiguity about "are these the right bytes," the hash proves it.
- **Bytes uploaded with no metadata row at all (orphan):** cannot happen under this ordering *from the artifact-creation path itself* (metadata always precedes upload), but can arise from a botched manual operation, a bug, or a previous version's data. Reconciler's second responsibility: periodically list storage keys and cross-check against `artifacts` rows; a key with no corresponding row (or only a `FAILED` row) past a grace period is an orphan, logged for operator review. V0.5 does not auto-delete orphans (deletion is a one-way door; flagging for review is the safe default -- consistent with this project's "don't build tooling for a problem you haven't proven needs solving" discipline applied to a genuinely destructive operation).

**Required clarification: the precise definition of `UPLOADED`.** `status='UPLOADED'` in Postgres is not, by itself, sufficient evidence that an artifact is usable -- object *existence* alone doesn't establish that the object at the key is the object that was supposed to be there. The invariant is stated precisely:
> **An artifact is usable if and only if: (1) its metadata row has `status='UPLOADED'`, AND (2) an object exists at `storage_key`, AND (3) that object's own content hash equals `artifacts.content_hash`.**
In practice, condition (1) is only ever set *after* conditions (2) and (3) are verified (the upload-completion flow in ARTIFACT_LIFECYCLE_V0.5.md re-hashes the uploaded object before flipping status) -- so `status='UPLOADED'` is meant to *imply* (2) and (3) held at the moment it was set. This is why the Reconciler's self-heal path re-verifies the hash rather than trusting object-existence alone (ARTIFACT_LIFECYCLE_V0.5.md) -- and, more importantly, why any consumer that needs a *stronger, real-time* guarantee (not merely trusting a Postgres flag set at some point in the past) can independently re-verify by re-hashing the object at read time. V0.5 does not require every read to re-verify (that would be expensive for large artifacts read frequently) -- it requires that the flag was only ever set after verification, which is what "usable" actually rests on.

## Why this is not "eventually consistent" hand-waving
Every state in the machine is defined by what a query can observe *right now*, not by a promise about the future:
- `PENDING` -- intent recorded, outcome unknown, **not usable** (nothing may reference it).
- `UPLOADED` -- bytes confirmed present and hash-verified, **usable**.
- `FAILED` -- bytes confirmed absent past the grace period, **not usable, terminal**.
Nothing downstream (a `ModelVersion`, a lineage query) ever has to reason about "maybe it's actually done, we're just not sure yet" -- it only ever sees `UPLOADED` as usable, full stop. The Reconciler's job is entirely about converging `PENDING` rows to one of the two terminal states as fast as operationally reasonable; it is not on the critical path for correctness, only for eventual resolution of ambiguous state -- exactly the same relationship V0.3's Recovery process has to a stuck `RUNNING` job (ADR 004's "an expired lease does not imply automatic recovery" posture, applied here as "a `PENDING` artifact does not imply automatic failure or success until reconciled").

## Failure scenarios answered directly
- **"Artifact upload succeeds but metadata transaction fails"** -- cannot happen under this ordering (metadata precedes upload) *for the creation path*; the closest real analog is the status-flip UPDATE failing after a successful upload, handled by Reconciler self-heal above.
- **"Metadata transaction succeeds but artifact upload fails"** -- the primary case this ADR solves; `PENDING` -> Reconciler -> `FAILED` (bytes genuinely never arrived) or `UPLOADED` (self-heal, bytes did arrive, only the flip was lost).
- **"Training succeeds but artifact upload fails"** -- the training run's own record shows a completed attempt with no `UPLOADED` artifact linked; this is a valid, representable state (a `TrainingRun` is not required to have produced a usable artifact) -- not an error condition requiring special-case handling, just an unfortunate but fully queryable outcome an operator can retry from.
- **"Artifact exists but training run fails"** -- e.g. the bytes uploaded successfully but a later step (evaluation, cleanup) caused the run to be marked failed. The artifact's own status is independent of the training run's status -- an `UPLOADED` artifact remains a valid, referenceable artifact regardless of what its originating run's final state was; whether anyone *chooses* to register it as a `ModelVersion` is a separate decision (REQUIREMENTS_V0.5.md's "training completion != model registration").
- **"Partially uploaded artifact"** -- always `PENDING` (or reconciled to `FAILED`) until fully verified by hash; never `UPLOADED` with a size/hash mismatch, because the flip to `UPLOADED` includes hash verification as part of the same conditional check.

## Alternatives considered
- **Two-phase commit / distributed transaction coordinator (e.g. XA):** rejected outright -- neither Postgres's typical deployment nor S3-compatible object stores support this in a way that's practical or even fully correct; this exact rejection is the reason this ADR exists instead of a one-line "just use 2PC."
- **Write bytes first, then metadata:** rejected -- this ordering makes the "successfully uploaded, no metadata" case indistinguishable from a true orphan (garbage/test upload with no intent to ever register it), since there's no `PENDING` row recording that an upload was *expected*. Metadata-first preserves that distinction.
- **No reconciliation process; require manual intervention for any stuck `PENDING` row:** rejected -- this is a real, expected failure mode (any process can crash mid-upload), not a rare edge case; leaving it to manual ops from day one is avoidable operational burden this project's existing poller pattern (Outbox Relay/Recovery/Scheduler) already solves cheaply.

## Consequences
- A new process, the **Reconciler**, joins Outbox Relay/Worker/Recovery/Scheduler as the fifth poller-pattern process in this system -- same crash-tolerant-by-construction design (every action is a single conditional check-then-update, a mid-cycle crash leaves no partial state, next poll picks up where it left off).
- `GRACE_PERIOD_SECONDS` (for never-claimed rows) and `UPLOAD_LEASE_DURATION_SECONDS`/`UPLOAD_HEARTBEAT_INTERVAL_SECONDS` (for the upload lease, mirroring V0.3's `LEASE_DURATION_SECONDS`/`HEARTBEAT_INTERVAL_SECONDS`) are all configurable -- no specific "correct" value is claimed without real upload-time data (V1.0's job to inform this with load-test evidence).
- Orphan detection is logged, not auto-remediated -- deletion tooling is explicitly out of scope for V0.5 (REQUIREMENTS_V0.5.md non-goals).
- Every uploader (whether the synchronous request-handling path for small dataset uploads, or a future async path for large model checkpoints) must renew its upload lease on its own timer, independent of the actual byte-streaming work -- the identical structural constraint ADR 004 placed on worker heartbeats, now placed on uploaders.
