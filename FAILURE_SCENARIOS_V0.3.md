# Failure Scenarios and Acceptance Tests — V0.3

Each scenario: expected behavior, why, test plan. No claim ships without a corresponding test run (same rule as V0.1/V0.2).

## 1. Worker dies immediately after claiming
**Expected:** `lease_expires_at = claim_time + LEASE_DURATION_SECONDS`. No heartbeat ever arrives. Recovery reclaims once `now() > lease_expires_at`, old attempt marked `LOST` (transient), new attempt dispatched.
**Test:** claim a job manually (no heartbeat loop started), wait past lease duration, run one recovery cycle, assert the `attempts` row for the current `attempt_number` has `status=LOST` (reclaim itself does not advance `attempt_number` -- ADR 004 -- only the subsequent `claim()` does when a worker picks up the retry), job reaches a terminal state after the new attempt runs.

## 2. Worker dies after N heartbeats
**Expected:** lease measured from the *last* heartbeat, not claim time. Same reclamation outcome as scenario 1, just later.
**Test:** claim, send 2-3 heartbeats manually with delays, then stop; assert lease expiry timing tracks the last heartbeat's `lease_expires_at`, not the original claim's.

## 3. Slow-but-healthy worker
**Expected:** never reclaimed, regardless of total execution time, as long as heartbeats keep arriving faster than `LEASE_DURATION_SECONDS`.
**Test:** claim, run a background heartbeat loop at `HEARTBEAT_INTERVAL_SECONDS` while a simulated "slow" execution runs (sleep) for longer than `LEASE_DURATION_SECONDS`; run recovery cycles throughout; assert the job is never reclaimed (`attempt_number` unchanged) and eventually reaches `SUCCEEDED` from the *original* claim.

## 4. Heartbeat races with lease expiration
**Expected:** exactly one of {heartbeat renews lease, reclaim succeeds} happens for any given instant -- never both, never neither.
**Test:** concurrently (threads) run a heartbeat renewal and a reclaim attempt against a job whose lease is right at the expiry boundary; assert exactly one of the two conditional UPDATEs affects a row; assert the job's final `(lease_owner, attempt_number)` is internally consistent (matches whichever one won).

## 5. Two recovery processes race for the same stale job
**Expected:** exactly one reclaims (same proof pattern as V0.2's concurrent-worker-claim test).
**Test:** 5 threads simultaneously attempt the reclaim UPDATE for one stale job; assert exactly one succeeds (rowcount 1), others rowcount 0; assert the job's resulting status/lease fields are coherent (not left half-modified by a "losing" thread) and `attempt_number` is unchanged by reclaim (ADR 004 -- reclaim does not advance the token, only `claim()` does).

## 6. Original worker returns after its lease expires (the split-brain test)
**Expected:** the reclaimed/new attempt completes normally. The original worker's late terminal-commit write (`SUCCEEDED`, using its stale `attempt_number`) affects zero rows and must be discarded by the worker, not retried or logged as a conflict requiring resolution.
**Why this is the critical test:** this is the exact scenario ADR 004 exists to prevent. If this test fails, the fencing mechanism is broken and V0.3 cannot ship.
**Test:** claim a job as "worker-A" (attempt_number=1), simulate its lease expiring, run Recovery's reclaim (fences worker-A by moving status off `RUNNING`; `attempt_number` stays 1), then worker-B claims it (`attempt_number` advances to 2) and completes it to `SUCCEEDED`, *then* have worker-A attempt its own terminal-commit UPDATE using its stale attempt_number=1 -- assert rowcount 0, assert the job's final state is whatever worker-B produced, untouched by worker-A's late write. Also verified: worker-A's stale `FAILED` write and stale retry/requeue write are rejected the same way, not just its stale `SUCCEEDED` write.

## 7. Recovered job delivered twice (duplicate retry-dispatch message)
**Expected:** idempotent, same mechanism as V0.2 -- a message for a job no longer in `QUEUED` (because the first delivery already claimed it) is a no-op.
**Test:** dispatch one retry outbox event, redeliver it (or don't commit the Kafka offset, forcing redelivery); assert exactly one new `attempts` row for the new `attempt_number`, one execution.

## 8. Retry happens while cancellation is requested
**Expected:** a job cancelled while `status=QUEUED` and awaiting `next_retry_at` never spawns a new attempt -- the next claim attempt's `AND cancel_requested = false` condition fails to match, rowcount 0.
**Test:** put a job into the retry-wait state, set `cancel_requested=true` via the cancel endpoint, then attempt a claim (or run the recovery process's retry-dispatch, then a worker's claim) -- assert the claim fails (rowcount 0) and the job is separately transitioned to `CANCELLED` via the normal cancel path, never re-enters `RUNNING`.

## 8b. Cancellation races with Recovery reclaiming an orphaned job
**Expected:** worker dies (lease will expire) while `RUNNING`; user cancels before Recovery's next poll; Recovery's reclaim UPDATE branches on `cancel_requested` in the same atomic statement (ADR 004) and lands the job on `CANCELLED`, never `QUEUED` -- a cancelled-but-orphaned job must never re-enter the retry cycle. This is distinct from scenario 8 (cancellation racing a *claim*) -- here it races a *reclaim*.
**Test:** claim as worker-A, call cancel (sets `cancel_requested=true` while still `RUNNING`), expire the lease, run Recovery's reclaim -- assert final status is `CANCELLED` (not `QUEUED`), `next_retry_at` is `NULL`, and any further claim attempt fails.

## 9. Retryable (transient) failure
**Expected:** `RUNNING -> QUEUED`, `next_retry_at` set per the backoff formula, `attempts` row for the failed attempt has `status=FAILED`, `error_classification=transient`.
**Test:** run a `simulate_transient_failure` job, assert it returns to `QUEUED` with a correctly computed `next_retry_at`, assert it eventually succeeds within `MAX_ATTEMPTS` if a later attempt is configured to succeed, or lands in the DLQ if all attempts fail.

## 10. Permanent failure
**Expected:** `RUNNING -> FAILED` immediately, `dlq` row inserted in the same transaction, no retry attempted regardless of `attempt_number`.
**Test:** run a `simulate_permanent_failure` job with `attempt_number=1`; assert it goes straight to `FAILED` + `dlq` entry, `attempts` count stays at 1 (no retry attempt ever created).

## 11. Max retry attempts reached
**Expected:** after `MAX_ATTEMPTS` transient failures, the job goes to `FAILED` + DLQ instead of another retry.
**Test:** configure a job type that always fails transiently; run through `MAX_ATTEMPTS` attempts; assert the `(MAX_ATTEMPTS + 1)`-th would-be retry never happens -- job is `FAILED`, `dlq.total_attempts = MAX_ATTEMPTS`.

## 12. DLQ transition
**Expected:** `dlq` row records `job_id`, `last_attempt_number`, `last_error_message`, `last_error_classification`, `total_attempts`, `moved_to_dlq_at` -- all populated, queryable via `GET /v1/dlq`.
**Test:** trigger scenario 10 or 11, assert the `dlq` row's fields are all correctly populated and the row is returned by `GET /v1/dlq`.

## 13. Kafka duplicate delivery (unchanged guarantee, re-verified under the new attempt model)
**Expected:** same as V0.2's proven live behavior, extended to be attempt-number-aware -- a duplicate retry-dispatch message for an already-claimed attempt is a no-op.
**Test:** re-run the equivalent of V0.2's live duplicate-delivery verification, but for a *retry* dispatch (not just the original claim), confirming the guarantee holds across the new code path too.

## 14. PostgreSQL failure during recovery
**Expected:** Recovery process's poll cycle fails closed (catches exception, logs, retries next cycle) -- no crash-loop, no data loss, no partial reclamation.
**Test:** stop Postgres mid-recovery-cycle, assert the Recovery process logs the error and continues polling once Postgres returns, assert no job was left in an inconsistent state (partial `attempt_number` increment without a matching `lease_owner`, for example -- should be structurally impossible since it's one atomic UPDATE, but the test proves it, not just assumes it).

## 15. Recovery process itself crashes mid-cycle
**Expected:** no corruption -- since every recovery action is a single atomic conditional UPDATE, a crash between two unrelated jobs' reclamations leaves the not-yet-processed stale job exactly as stale as before, reclaimable by the next cycle (this instance restarted, or another instance).
**Test:** kill the Recovery process after it reclaims job X but before it would reach job Y (both stale in the same cycle); restart Recovery; assert job Y gets reclaimed on the next cycle, assert job X was not double-reclaimed or corrupted by the crash.

## Clean-room Docker verification (required before v0.3.0 tag)
`docker compose down -v && docker compose up --build` -- API + Worker + Outbox Relay + **Recovery** (new) + Redpanda + Postgres. At minimum: a job whose worker is killed mid-execution (via `docker stop` on a scoped single-job test, or the manual crash-simulation technique used for V0.2's split-brain verification) is observed, via logs and DB state, to be correctly reclaimed and completed by a subsequent attempt -- end to end, not just at the unit-test level.
