# REQUIREMENTS — V0.3 Failure Recovery & Retry Engine

## Objective
Solve the V0.2-accepted gap (worker dies after claiming a job -> job stuck `RUNNING` forever) via heartbeats, leases, and safe stale-job reclamation, while adding a controlled retry engine (attempt tracking, classification, backoff, max attempts, DLQ) -- without breaking any V0.2 correctness guarantee (conditional-UPDATE claiming, idempotent execution, at-least-once transport).

V0.1 and V0.2 are tagged (`v0.1.0`, `v0.2.0`) and not modified except additively (new columns/tables, no removed guarantees).

## Functional requirements

1. **Worker heartbeat** -- while a worker holds a job, it periodically renews a lease (`HEARTBEAT_INTERVAL_SECONDS`, default 5s), on a schedule independent of how long the execution body itself takes (see ARCHITECTURE_V0.3.md -- heartbeats must not be blocked by a long-running execution body).
2. **Job lease** -- claiming a job (first attempt or retry) grants a lease: `lease_owner`, `lease_expires_at = now() + LEASE_DURATION_SECONDS` (default 30s).
3. **Lease expiration + stale-job detection** -- a new **Recovery** process polls for `RUNNING` jobs whose `lease_expires_at < now()` and treats them as candidates for reclamation.
4. **Safe stale-job reclamation** -- reclamation must not allow two processes to both believe they now own the job, and must not allow the original (possibly still-alive, partitioned) worker to later commit a result as if it still owned the job. See ADR 004 for the fencing-token mechanism that guarantees this.
5. **Attempt tracking** -- replace V0.2's `executions` (one row per job) with `attempts` (one row per attempt, `(job_id, attempt_number)`), per the migration path ADR 003 already promised.
6. **Retry policy** -- on a `FAILED` outcome, classify the failure (transient / permanent / unknown) and decide: retry with backoff, or fail permanently into the DLQ. See ADR 005.
7. **Exponential backoff** -- `next_retry_at = now() + min(BASE_DELAY_SECONDS * 2^(attempt_number-1), MAX_DELAY_SECONDS)`.
8. **Maximum attempts** -- `MAX_ATTEMPTS` (default 3). Reaching it on a transient failure forces a permanent `FAILED` + DLQ entry, not another retry.
9. **Dead-letter queue** -- a job that exhausts retries or hits a permanent-classified failure gets a `dlq` row recording enough to answer: which job, which attempt, why, which worker, when, how many attempts, last error.
10. **Recovery/idempotency guarantees** -- every mechanism from V0.2 (conditional UPDATE claiming, execution-record idempotency) still holds; V0.3 extends the fencing key from `job_id` alone to `(job_id, attempt_number, lease_owner)`.

## Non-functional requirements
- No Kubernetes, Redis, Ray, GPU scheduling, autoscaling, priority scheduling, multi-region infra, workflow DAGs.
- Recovery process, like the V0.2 outbox relay, must be crash-tolerant by construction: every action is a single atomic conditional UPDATE, so a mid-cycle crash leaves no partial state, only a job that'll be picked up again next poll.
- `LEASE_DURATION_SECONDS` and `HEARTBEAT_INTERVAL_SECONDS` are configurable env vars; ARCHITECTURE_V0.3.md documents the tradeoff and the operational rule `LEASE_DURATION_SECONDS >= HEARTBEAT_INTERVAL_SECONDS * safety_factor` (recommended factor: 3, mirroring V0.1's own "3 missed heartbeats" precedent).

## The hard invariant (must be true, and must be tested)
> An expired lease does not imply the original worker has stopped executing. The system must make it impossible for the original worker to commit a terminal result (`SUCCEEDED`/`FAILED`/`CANCELLED`) for a job after that job's ownership has been reclaimed by another process -- even if the original worker never learns it was fenced out.

This is the split-brain problem the design must solve structurally, not by convention. See ADR 004.

## Acceptance criteria (must all pass before v0.3.0 tag)
- [ ] Worker dies immediately after claiming (before any heartbeat) -> job reclaimed after `LEASE_DURATION_SECONDS`, old attempt marked `LOST`, new attempt created, job eventually reaches a correct terminal state
- [ ] Worker dies after N heartbeats -> same outcome, lease measured from last heartbeat, not from claim time
- [ ] Slow-but-healthy worker (heartbeats keep flowing during a long execution body) -> never reclaimed
- [ ] Heartbeat racing lease expiration -> exactly one of {heartbeat extends lease, reclaim succeeds} happens, never both, proven via concurrent test
- [ ] Two recovery processes racing the same stale job -> exactly one reclaims (same conditional-UPDATE proof pattern as V0.2's concurrent-worker test)
- [ ] **Split-brain test (the critical one):** original worker's lease expires, job is reclaimed and a new attempt completes, then the *original* worker attempts to commit its own (stale) terminal result -> rejected, does not corrupt the already-finalized job
- [ ] Recovered job's retry-dispatch message delivered twice -> idempotent, one execution of the new attempt
- [ ] Cancellation requested while a job is queued for retry -> next claim attempt short-circuits into `CANCELLED`, no new attempt spawned
- [ ] Transient failure -> retries with correct exponential backoff timing
- [ ] Permanent failure -> immediate `FAILED` + DLQ, no retry attempted
- [ ] Max attempts reached on repeated transient failures -> `FAILED` + DLQ, not an infinite retry loop
- [ ] Postgres failure during a recovery poll cycle -> fails closed, self-heals next cycle, no data loss
- [ ] Recovery process itself killed mid-cycle -> no corruption, stale job still reclaimable on next run (by itself or another instance)
- [ ] All new numbers/claims trace to actual test/run output (same rule as V0.1/V0.2)
