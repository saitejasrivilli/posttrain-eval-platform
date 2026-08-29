# Job State Transition Documentation — V0.3

## States (unchanged set from V0.2)
`PENDING`, `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`. No new **job-level** states in V0.3. `LOST` exists only as an `attempts.status` value (ADR 006), never as a `jobs.status` value -- the job itself is always `RUNNING` (from its perspective) until the Recovery process's reclaim UPDATE moves it to `QUEUED` (if retryable) or `FAILED` (if attempts exhausted), same as any other retry outcome. This keeps the job-level state machine exactly as small as V0.2's; `LOST` is purely an attempt-history/classification concept.

## Full lifecycle including the infrastructure-failure path
```
QUEUED
   |
   v
RUNNING
   |
   +-- execution succeeds --------------------> SUCCEEDED
   |
   +-- execution fails, permanent ------------> FAILED -> DLQ
   |
   +-- worker lease expires (Recovery reclaims) -> old attempt marked LOST (attempts table)
   |                                                  |
   |                                                  v
   |                                            retryable? (attempt_number < MAX_ATTEMPTS)
   |                                             /                  \
   |                                          yes                   no
   |                                           |                     |
   |                                           v                     v
   |                                        QUEUED                FAILED -> DLQ
   |                                     (next_retry_at set)
   |
   +-- cancel_requested observed at checkpoint -> CANCELLED
```
`LOST` in this diagram labels the *attempt's* fate, not a stop the job's own status passes through -- the job's status transitions directly `RUNNING -> QUEUED` or `RUNNING -> FAILED` in the same reclaim transaction that writes the old attempt's `LOST` record.

## Valid transitions (V0.3 additions/changes over STATE_TRANSITIONS_V0.2.md)

| From | To | Trigger | Actor | Fencing-conditioned? |
|---|---|---|---|---|
| QUEUED | RUNNING | claim (first attempt or fresh retry attempt) | Worker | Yes -- increments `attempt_number` (the only place it advances) |
| RUNNING | QUEUED/FAILED/CANCELLED | stale-lease reclaim | Recovery | Yes -- does NOT increment `attempt_number`; fences the old owner purely by moving `status` away from `RUNNING` (see ADR 004) |
| RUNNING | SUCCEEDED | execution completes | Worker | Yes -- `(lease_owner, attempt_number)` must match |
| RUNNING | FAILED | permanent-classified failure, or transient failure at `MAX_ATTEMPTS` | Worker | Yes |
| RUNNING | **QUEUED** (new) | transient-classified failure, attempts remain | Worker | Yes |
| RUNNING | CANCELLED | worker observes `cancel_requested` at checkpoint | Worker | Yes |
| QUEUED | CANCELLED | user cancels before claim (unchanged from V0.2) | API | N/A (no lease held yet) |

**Important:** `RUNNING -> QUEUED` is new in V0.3 and represents "this attempt failed transiently, waiting to retry" -- distinct from the job's original `PENDING -> QUEUED` (V0.2). A job can cycle `QUEUED <-> RUNNING` multiple times, each cycle incrementing `attempt_number`, bounded by `MAX_ATTEMPTS`.

## Invalid transitions (unchanged rule, extended coverage)
Everything not in the table above is rejected, including:
- Any transition attempted by a worker whose `(lease_owner, attempt_number)` no longer matches the row -- this is the *fencing* rejection (ADR 004), distinct from a *state-machine* rejection (attempting a transition not in the table) but implemented by the same mechanism: the conditional UPDATE's WHERE clause encodes both the valid-state-transition rule and the ownership rule simultaneously.
- `RUNNING -> QUEUED` past `MAX_ATTEMPTS` -- must go to `FAILED` instead; a worker that tries to re-queue an exhausted job is a bug, not a legitimate path (the retry-decision logic in ADR 005 is what prevents this from ever being attempted, not a separate DB constraint).

## Terminal states (unchanged)
`SUCCEEDED`, `FAILED`, `CANCELLED`. Reaching `FAILED` via exhausted retries or permanent classification is followed by a `dlq` row insert in the same transaction as the terminal write -- the job's own status transition and its DLQ entry are atomic together.

## Ownership as a parallel, orthogonal concept to state
A job's *status* (`RUNNING`, etc.) answers "what state is this job in." A job's *(lease_owner, attempt_number)* answers "who, if anyone, currently owns it, and does a given worker's belief about that match reality." These are checked together in every conditional UPDATE, but conceptually distinct: V0.2's state machine (STATE_TRANSITIONS_V0.2.md) was a single-dimension check (current status). V0.3 makes every `RUNNING`-sourced transition a *two*-dimension check (status AND ownership) -- both must hold, and the database enforces both in one atomic statement, never as two separate checks that could race.

## Cancellation and retry interaction (new in V0.3)
Per REQUIREMENTS_V0.3.md: a job cancelled while waiting for its next retry (`status=QUEUED`, `next_retry_at` in the future) must not spawn a new attempt. The claim UPDATE (ADR 004) includes `AND cancel_requested = false` in its WHERE clause -- a cancel request arriving during the retry-wait window is caught the next time anything attempts to claim the job, and that claim simply fails to match (rowcount 0), same mechanism as every other fencing/state check. No special-case code path is needed; cancellation is just one more condition in the same conditional UPDATE.

**Both race orderings must be deterministic and tested, not just one:**
- *cancel arrives, then a claim is attempted:* claim's `cancel_requested=false` condition fails to match -> claim rejected, job later transitions `QUEUED -> CANCELLED` via the normal cancel path.
- *a claim is attempted, then cancel arrives:* claim already committed (row now `RUNNING` under a new attempt) before the cancel request lands -> cancel falls into the existing `RUNNING` path (sets `cancel_requested=true`, cooperative checkpoint per V0.2), not the fast `QUEUED -> CANCELLED` path. Whichever database transaction commits first wins, deterministically, because both are single atomic statements -- there is no partial-application window.

**`LOST` + `cancel_requested` must not resurrect a cancelled job:** if `cancel_requested=true` was set while an attempt was `RUNNING`, and that same attempt's lease then expires (worker died without ever checking the flag), the Recovery process's reclaim UPDATE (ADR 004) branches on `cancel_requested` in the *same* atomic statement that performs the ownership move -- `status` is set directly to `CANCELLED` (not `QUEUED`) whenever `cancel_requested` is true, regardless of remaining attempts. The old attempt is still recorded `LOST` in `attempts` for audit purposes, but the job itself never re-enters the retry cycle. This must be explicitly tested (FAILURE_SCENARIOS_V0.3.md).

## Relationship to V0.2
V0.2 (STATE_TRANSITIONS_V0.2.md) declared `RUNNING` as reachable only from `QUEUED`, transitioning out only to `SUCCEEDED`/`FAILED`/`CANCELLED`, all terminal-from-the-job's-perspective. V0.3 does not contradict this -- it adds a new edge (`RUNNING -> QUEUED`) and a new actor (Recovery) for one existing edge (`QUEUED -> RUNNING` self-loop via reclaim, same target state, different attempt). No V0.2 transition is removed or made stricter in a way that would reject something V0.2 previously allowed.
