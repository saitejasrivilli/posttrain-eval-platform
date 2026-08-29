# Job State Transition Documentation — V0.4

## No new job-level states
V0.4 does not add, remove, or rename any `jobs.status` value from V0.2/V0.3 (`PENDING`, `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`). A reservation existing is orthogonal metadata about a `QUEUED` job -- exactly the same relationship V0.3's `(lease_owner, attempt_number)` has to `RUNNING` (STATE_TRANSITIONS_V0.3.md's "ownership as a parallel, orthogonal concept to state"). No `SCHEDULED` or `RESERVED` job status is introduced -- a reserved-but-not-yet-claimed job is still, correctly, `QUEUED`.

## Where the Scheduler sits in the existing lifecycle
```
QUEUED (eligible: not cancelled, retry-backoff elapsed -- V0.3's claim() preconditions)
   |
   v
[Scheduler admits: reservation created, job STAYS QUEUED]
   |
   v
claim() (V0.3, extended): now ALSO requires the reservation to exist
   |
   v
RUNNING -- unchanged from V0.3 (heartbeat, lease, fencing)
   |
   +-- SUCCEEDED / FAILED / CANCELLED -- reservation released (new)
   +-- lease expires -> Recovery reclaims -- reservation released (new), attempt LOST
```
A job can be `QUEUED` in three distinguishable (but not separately-status-tracked) sub-states: not-yet-eligible (retry backoff), eligible-but-unreserved (waiting on the Scheduler), and eligible-and-reserved (waiting on a Worker to claim it). None of these get their own `jobs.status` value -- they're derivable from `next_retry_at`, whether a `reservations` row exists, same principle as V0.3 deriving "is this job's retry due" from a timestamp rather than a new status.

## Reservation lifecycle (new, but not a job-status lifecycle)
```
(no reservation)
   |
   v  Scheduler admits (ADR 007)
status=ACTIVE, created_at set, released_at=NULL
   |
   v  job reaches SUCCEEDED/FAILED/CANCELLED, or Recovery marks the attempt LOST
status=ACTIVE -> RELEASED (conditional UPDATE, exactly one winner -- see invariant below)
released_at set
```
`ACTIVE -> RELEASED` is itself a fencing-style conditional transition (`WHERE status='ACTIVE'`), not merely "set a timestamp if it's null" -- this is what gives release its idempotency (DB_SCHEMA_CHANGES_V0.4.md). `capacity.allocated_*` is decremented only by whichever caller's UPDATE actually wins that transition. A reservation is never "reused" across attempts -- a retried job gets a fresh reservation scoped to its new `attempt_number` when the Scheduler admits it again (ADR 007/RESOURCE_MODEL_V0.4.md).

## Invariants this version adds
- **No scheduling of cancelled jobs:** the Scheduler's re-verification step (ADR 007) checks `cancel_requested=false` at admission time using the identical condition `claim()` uses -- a job cancelled between being ranked and being reserved simply fails the atomic reservation transaction's re-check (rolled back, no reservation created), same "one atomic statement is the whole enforcement" pattern as everywhere else in this project.
- **No scheduling of retry-not-yet-due jobs:** same mechanism, `next_retry_at` condition.
- **No worker execution without a valid reservation:** `claim()`'s extended precondition (ARCHITECTURE_V0.4.md).
- **No double reservation:** a job can have at most one *unreleased* reservation at a time -- enforced by scoping reservations to `(job_id, attempt_number)` and only ever creating one per attempt (the Scheduler's admission transaction only fires for currently-`QUEUED`-and-unreserved jobs; a job that already has an unreleased reservation for its current attempt is not re-offered to the ranking step at all).
- **No double release:** release is itself a conditional UPDATE (`WHERE released_at IS NULL`) -- a second release attempt (e.g. both a worker's normal finalize AND a racing Recovery reclaim somehow both trying to release the same reservation) affects zero rows the second time, same fencing-style idempotency V0.3 established for attempts.

## Relationship to V0.2/V0.3
No V0.2 or V0.3 transition is removed, renamed, or made stricter in a way that rejects something previously allowed. V0.4 only adds a precondition to `claim()` and adds a release side-effect to existing terminal/reclaim writes -- both additive, not redesigns, consistent with this project's version-to-version discipline since V0.1 ADR 001.
