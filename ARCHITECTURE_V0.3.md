# ARCHITECTURE — V0.3 (updated HLD)

Delta on top of ARCHITECTURE.md (V0.1) and ARCHITECTURE_V0.2.md (V0.2). Neither is redesigned; V0.3 adds one new process and extends the worker.

## Component diagram

```
                 Client (curl/tests)
                        |
                        v
                 FastAPI app (unchanged from V0.2)
                        |
                        v
                 Service / Repository
                        |
                        v
                  PostgreSQL
        (jobs, outbox, attempts, dlq tables)
                    ^   ^    ^
                    |   |    |
      +-------------+   |    +-------------------+
      |                 |                        |
+------------+   +--------------+       +------------------+
| Outbox     |   | Worker (N)   |       | Recovery (new)   |
| Relay      |   | claim -> run |       | poll stale leases|
| (V0.2,     |   | -> heartbeat |       | -> reclaim       |
| unchanged) |   | (background) |       | poll retry-due   |
|            |   | -> finalize  |       | -> dispatch      |
+------------+   | (fenced)     |       +------------------+
      |          +--------------+                |
      v                 |                         |
   Kafka  <-------------+-------------------------+
(job.queued)
```

Four OS processes now: **API**, **Outbox Relay** (unchanged), **Worker** (extended: heartbeat loop + fencing-conditioned writes), **Recovery** (new: stale-lease reclamation + retry dispatch). All talk to Postgres directly through the shared repository module, consistent with V0.1/V0.2's "single deployable domain logic, multiple entrypoints" decision -- no new service boundary invented.

## Worker internals (extended)
```
claim (fencing-conditioned UPDATE, attempt_number+1 -- the only place the token advances)
  |
  +-- spawn heartbeat loop (background thread, independent timer,
  |   HEARTBEAT_INTERVAL_SECONDS) -- renews lease_expires_at, fencing-
  |   conditioned on (lease_owner, attempt_number). If a renewal affects
  |   zero rows, the worker has been fenced: it sets a local "abandoned"
  |   flag and the execution path must check it before any terminal write.
  |
  +-- run execution body (still simulated in V0.3; real bodies are V0.6+)
  |
  +-- finalize (fencing-conditioned UPDATE): SUCCEEDED / FAILED (->retry
      or ->DLQ per ADR 005) / CANCELLED. If the "abandoned" flag is set,
      or the finalize UPDATE itself affects zero rows, the result is
      discarded -- never retried, never logged as a conflict to resolve.
```
The heartbeat loop's independence from the execution body is a hard architectural requirement (ADR 004): a slow-but-healthy worker must keep renewing its lease throughout a long execution, not just before/after it. This is enforced structurally, not by convention: the worker is composed of a lease-holder (heartbeat + fencing writes) and an executor behind an interface (V0.3: simulated in-process call; V0.6+: a supervised subprocess) -- see ADR 004's "interface separation" section.

## Recovery process (new)
Polls on `RECOVERY_POLL_INTERVAL_MS` (default 1000ms), two independent jobs per cycle:
1. **Stale-lease reclamation:** find `RUNNING` jobs with `lease_expires_at < now()`, attempt the fencing-conditioned reclaim UPDATE (ADR 004) for each. On success, insert an `attempts` row for the *old* attempt_number with `status=LOST`, `error_classification=transient`.
2. **Retry dispatch:** find `QUEUED` jobs with `next_retry_at <= now()` that haven't yet had an outbox event emitted for their current `attempt_number`, insert a new outbox row (payload includes `job_id` and `attempt_number`) in the same transaction pattern as V0.2's `create_and_enqueue`.

Both operations are single atomic conditional UPDATEs/INSERTs -- the Recovery process is crash-tolerant by construction, exactly like the V0.2 Outbox Relay: a mid-cycle crash leaves no partial state, the next poll (by this instance or a restarted one) picks up any still-stale job.

## Database ownership (updated)
- `jobs` -- extended with `attempt_number`, `lease_owner`, `lease_expires_at`, `next_retry_at`. Written by API (create/cancel, unchanged from V0.2), Worker (claim/heartbeat/finalize, fencing-conditioned), Recovery (reclaim/retry-dispatch, fencing-conditioned).
- `attempts` (replaces `executions`, see ADR 006) -- written by Worker (its own attempt's lifecycle) and Recovery (marks the superseded attempt `LOST`).
- `dlq` (new) -- written only by Worker, only when an attempt's failure is classified permanent or exhausts `MAX_ATTEMPTS`.

## Config additions
`LEASE_DURATION_SECONDS` (30), `HEARTBEAT_INTERVAL_SECONDS` (5), `RECOVERY_POLL_INTERVAL_MS` (1000), `MAX_ATTEMPTS` (3), `BASE_DELAY_SECONDS` (2), `MAX_DELAY_SECONDS` (60), `JITTER_RATIO` (0.2). Operational rule: `LEASE_DURATION_SECONDS >= HEARTBEAT_INTERVAL_SECONDS * 3` -- leaves margin so one delayed heartbeat (transient latency, brief GC pause) doesn't trigger a false reclamation; only sustained silence across multiple missed heartbeats does. Not code-enforced in V0.3 (documented convention, same as V0.1's original "3 missed heartbeats" precedent), tunable once real load data exists (V1.0).

## Claim/reclaim precondition (complete list)
The claim UPDATE (ADR 004) is conditioned on **all** of: `status IN ('QUEUED')` (or `RUNNING` + expired lease for reclaim), `cancel_requested = false`, and -- new since the retry-timing clarification -- `next_retry_at IS NULL OR next_retry_at <= now()`. A job whose backoff hasn't elapsed yet cannot be claimed by anything, which is what makes `next_retry_at` an enforced schedule rather than an informational timestamp (see ADR 005).

## Explicitly NOT introduced in V0.3
Kubernetes, Redis, Ray, GPU scheduling, autoscaling, priority/fairness scheduling, multi-region infrastructure, workflow DAGs, DLQ redrive/processing tooling, jittered backoff (deferred pending load-test evidence).
