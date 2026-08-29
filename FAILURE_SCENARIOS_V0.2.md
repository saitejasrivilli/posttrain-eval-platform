# Failure Scenarios and Acceptance Tests — V0.2

Each scenario: expected behavior, why, and the test that proves it. No number/claim ships without a corresponding test run (same rule as V0.1).

## 1. DB commit succeeds but Kafka publish fails
**Expected:** Job persists as `QUEUED`. Outbox row persists `published_at=NULL`. No data loss — relay retries on next poll, publishes once Kafka is reachable again.
**Why:** Outbox pattern (ADR 002) — publish is decoupled from the commit that matters.
**Test:** Integration test creates a job with Kafka container stopped; asserts job + outbox row exist in Postgres; starts Kafka; asserts outbox row's `published_at` becomes non-null within a bounded poll window; asserts worker eventually receives and processes it.

## 2. Kafka publish succeeds but consumer (worker) crashes
**Expected:** Message redelivers to another consumer in the group (or the same one on restart) per Kafka's normal consumer-group rebalance. Worker re-checks job status before acting — if job never got claimed (still `QUEUED`), it claims and runs normally. If it crashed *after* claiming (see #5), that's the stuck-`RUNNING` known gap.
**Why:** Kafka's own redelivery-on-no-commit is sufficient here; the interesting risk is not message loss but re-execution, which idempotency (ADR 003) already covers.
**Test:** Kill worker process before it commits Kafka offset but after receiving message; restart worker; assert job still processes exactly once (via `executions` row count = 1).

## 3. Duplicate Kafka delivery
**Expected:** Exactly one execution recorded; second delivery is a no-op ack.
**Why:** ADR 003 — job status check + `executions` unique constraint.
**Test:** Manually publish the same `job.queued` message twice (or don't commit offset, forcing redelivery); assert `executions` table has exactly one row for that `job_id`; assert job's `updated_at` doesn't change on the second (no-op) pass beyond the first execution.

## 4. Two workers receive the same job
**Expected:** Exactly one worker successfully claims (`QUEUED -> RUNNING` UPDATE rowcount 1); the other gets rowcount 0 and no-ops.
**Why:** ADR 003 claim-level concurrency control — Postgres row locking on the conditional UPDATE.
**Test:** Spin up 2+ worker processes in docker-compose pointed at the same message (simulate via same consumer group processing overlap, or directly call the claim function concurrently from a test with threads/processes); assert exactly one `RUNNING` transition succeeded, assert `executions` has exactly one row.

## 5. Worker crashes after acquiring a job
**Expected (V0.2 accepted gap):** Job remains `RUNNING` indefinitely. No automatic recovery — that's V0.3's heartbeat/staleness detection. `claimed_at` timestamp lets an operator manually identify stuck jobs.
**Why:** Documented known gap (REQUIREMENTS_V0.2.md) — building heartbeat detection now would be scope creep into V0.3's explicitly-planned territory.

**Invariant (must hold, not just be "acceptable"):**
> A `RUNNING` job with no active worker is recoverable **only** through the V0.3 stale-worker recovery mechanism. V0.2 has no code path that automatically transitions an orphaned `RUNNING` job to any other state. This is a dangerous-looking but intentional state — not a bug, not silently swept under "known gap" without a hard guarantee attached: the job must never be auto-marked `SUCCEEDED`/`FAILED` by anything in V0.2 just because time passed or a new worker started.

**Test:** Kill worker mid-execution (after claim, before terminal transition); assert job stays `RUNNING`; restart a fresh worker process and let it run its normal consume loop; assert the orphaned job is *not* touched, not re-claimed, not transitioned to `SUCCEEDED`/`FAILED`/anything else — it stays `RUNNING` indefinitely, proving no accidental partial-recovery logic exists.

## 6. Cancellation races with worker execution
**Expected:** If cancel request arrives while job is still `QUEUED`, atomic UPDATE cancels it before any worker claims it (worker's later claim attempt gets rowcount 0, no-ops). If cancel arrives after `RUNNING` has already started, `cancel_requested` flag is set but the in-flight execution completes its current unit of work; worker checks the flag at its next checkpoint and transitions to `CANCELLED` instead of `SUCCEEDED`/`FAILED` if seen — but if the worker already passed its last checkpoint before the flag was set, the job completes normally despite the cancel request (last-checkpoint-wins, documented as accepted behavior, not a bug).
**Why:** True mid-execution preemption requires actual checkpointable execution bodies, which don't exist until V0.6 trains real jobs — V0.2's executor is a no-op/simulated body specifically so this race can be tested without pretending to solve a problem that doesn't have real substance yet.
**Test:** (a) cancel a `QUEUED` job concurrently with a worker claim attempt — assert cancel wins if it commits first, claim wins if it commits first, but never both; (b) cancel a `RUNNING` job before its checkpoint — assert final status is `CANCELLED`; (c) cancel a `RUNNING` job after its checkpoint — assert final status is whatever the execution outcome was, and this is asserted as *expected*, not a failure.

## 7. Kafka becomes unavailable
**Expected:** API continues accepting job creation (writes to Postgres succeed independent of Kafka). Outbox rows accumulate unpublished. No job creation request fails due to Kafka being down.
**Why:** Outbox decouples the two — this is the entire point of ADR 002.
**Test:** Stop Kafka container; create N jobs via API; assert all N succeed with 201; assert N outbox rows exist unpublished; start Kafka; assert all N eventually publish and get processed.

## 8. PostgreSQL becomes unavailable
**Expected:** Matches V0.1 behavior — `/readyz` returns 503. API job-creation requests fail closed (5xx, not silently queued in memory). Worker and Outbox Relay also fail closed — no in-memory buffering of job state anywhere in the system (consistent with V0.1 ARCHITECTURE.md's "don't put durable job state in memory" rule, now applied to two new processes as well).
**Why:** Postgres is the single source of truth; nothing may proceed as if a job exists without it being durably recorded there first.
**Test:** Stop Postgres container; assert `/readyz` returns 503; assert job creation returns 5xx; assert worker process logs connection errors and does not crash-loop or silently drop in-flight claims; restart Postgres; assert system recovers without manual intervention (matches V0.1's DB-recovery precedent).

## Clean-room Docker verification (required before v0.2.0 tag)
`docker compose down -v && docker compose up --build` — brings up API + Worker + Outbox Relay + Kafka + Postgres from nothing. Full async lifecycle (`create -> QUEUED -> RUNNING -> SUCCEEDED`) observed end-to-end via curl (`POST` then poll `GET`) and via structured logs from all three app processes, not just unit-level trust.
