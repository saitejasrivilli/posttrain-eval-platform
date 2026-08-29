# Artifact Lifecycle — V0.5

Operational specification of ADR 013's state machine.

## States
```
PENDING   -- metadata row exists, upload outcome unknown, NOT usable by anything
UPLOADED  -- bytes confirmed present, hash-verified, usable (may be referenced
             by DatasetVersion/ModelVersion)
FAILED    -- bytes confirmed absent past grace period, terminal, NOT usable
```

## Transitions
| From | To | Trigger | Actor |
|---|---|---|---|
| (none) | PENDING | artifact creation begins (before upload) | API/service handling the upload request |
| PENDING | UPLOADED | upload completes, hash verified | Same request handler (happy path) OR Reconciler (self-heal path, ADR 013) |
| PENDING | FAILED | upload confirmed never completed, past grace period | Reconciler only |

No transition out of `UPLOADED` or `FAILED` -- both terminal. A "failed" upload is not retried in-place; a new upload attempt creates a new `PENDING` row (new artifact record), consistent with content-addressing (if the retry produces identical bytes, it converges on the same storage key/hash anyway -- ADR 011).

## The hard invariant
**No `DatasetVersion` or `ModelVersion` may ever reference an artifact whose status is not `UPLOADED`.** Enforced at the query/service layer (any creation of a `DatasetVersion`/`ModelVersion` re-checks the artifact's current status inside the same transaction, same "re-verify eligibility" pattern V0.4's Scheduler uses before reserving). This is the artifact-domain equivalent of V0.4's "worker cannot claim without a valid reservation" -- a downstream consumer cannot be constructed from an unconfirmed dependency.

**Precise meaning of `UPLOADED` (ADR 013):** metadata says `UPLOADED` AND an object exists at `storage_key` AND that object's own hash equals `artifacts.content_hash`. `status='UPLOADED'` is only ever set after verifying (2) and (3), never before.

## Upload ownership: the upload lease (ADR 013, reuses ADR 004's mechanism)
An uploader must **claim** a `PENDING` row before uploading, and **renew** the claim on its own timer while streaming -- this is what lets the Reconciler distinguish "actively being uploaded" from "abandoned," instead of guessing from `created_at` age alone.
```
claim (atomic UPDATE): WHERE id=:id AND status='PENDING'
    AND (upload_lease_expires_at IS NULL OR upload_lease_expires_at < now())
  SET uploader_id=:uploader_id, upload_lease_expires_at=now()+UPLOAD_LEASE_DURATION_SECONDS
```
Same shape as `jobs.claim()` (V0.3) -- rowcount 0 means someone else already holds the lease (or it's not `PENDING` anymore); rowcount 1 means this call now owns the upload. The uploader renews this lease on `UPLOAD_HEARTBEAT_INTERVAL_SECONDS`, on its own timer, independent of the byte-streaming work in progress (ADR 004's structural requirement, restated for uploads).

## Upload flow (happy path)
```
1. claim the upload lease (above)
2. compute content hash of the bytes while streaming (ADR 011 -- never load a
   multi-GB artifact fully into memory just to hash it; stream -> hash -> upload)
3. upload bytes to storage_key (derived from the hash)
4. verify: re-read/HEAD the object, confirm its hash matches artifacts.content_hash
5. conditional UPDATE artifacts SET status='UPLOADED', uploaded_at=now(),
   upload_lease_expires_at=NULL
   WHERE id=:id AND status='PENDING' AND uploader_id=:uploader_id
```
Step 5 is fencing-conditioned on `uploader_id` matching (same ADR 004 pattern) -- an uploader whose lease already expired and was reclaimed cannot flip the row; its update simply no-ops (rowcount 0), and its result must be discarded, never retried, exactly like a fenced-out V0.3 worker.

## Reconciliation flow (lease-aware sweep)
```
for each artifacts row WHERE status='PENDING' AND (
      (upload_lease_expires_at IS NULL AND created_at < now() - GRACE_PERIOD_SECONDS)
      OR (upload_lease_expires_at IS NOT NULL AND upload_lease_expires_at < now())
    ):
    check object storage for storage_key
    if present and hash matches artifacts.content_hash:
        conditional UPDATE PENDING -> UPLOADED (self-heal)
    else:
        conditional UPDATE PENDING -> FAILED
```
A `PENDING` row with a still-live, unexpired lease is **never** touched by the Reconciler -- this is the precise rule that closes the abandonment race: if an uploader claims the lease and keeps renewing it, the Reconciler's own precondition simply never matches that row, no matter how long the upload legitimately takes.

## The two races that must be explicitly tested
1. **Uploader active, Reconciler runs concurrently:** uploader claims the lease before (or renews faster than) the Reconciler's sweep interval; Reconciler's precondition excludes the row (lease still valid); uploader completes normally; final state is `UPLOADED`, never touched by Reconciler.
2. **Uploader crashes before claiming (or after its lease has already lapsed), Reconciler runs:** row has no valid lease; Reconciler correctly treats it as abandoned, checks storage (finds nothing, since the uploader never got far enough), marks `FAILED`.

## Orphan detection flow (lower-frequency sweep)
```
for each object key under the artifacts prefix in storage:
    if no artifacts row exists with this storage_key, OR the row is FAILED,
       AND the object's age exceeds the grace period:
        log as orphan for operator review (no auto-deletion, ADR 013)
```

## Relationship to job/attempt cancellation
If a training job is cancelled (V0.2/V0.3's existing cancellation path) while its artifact is `PENDING`, the artifact is not automatically transitioned -- it follows the same grace-period reconciliation as any other stuck `PENDING` row (typically resolving to `FAILED`, since a cancelled job's execution body won't complete the upload). No special-case cancellation-aware artifact logic is introduced; the existing state machine already produces the correct outcome.
