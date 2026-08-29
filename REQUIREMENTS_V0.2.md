# REQUIREMENTS — V0.2 Durable Asynchronous Job Execution

## Objective
Turn V0.1's synchronous job CRUD into a durable async execution system. Postgres stays source of truth; Kafka is transport only. V0.1 API/behavior not modified except where V0.2 explicitly extends it.

## Functional requirements

1. **Explicit job state machine** (enforced, unlike V0.1 where it was documented-only)
   ```
   PENDING -> QUEUED -> RUNNING -> SUCCEEDED
                     \-> RUNNING -> FAILED
   QUEUED -> CANCELLED
   RUNNING -> CANCELLED   (best-effort, see cancellation race section)
   ```
   Illegal transitions rejected at the repository layer with a typed error, not silently accepted (V0.1's PATCH accepted anything — that gap closes now).

2. **Transactional outbox**
   - Job creation and "event to publish" are written in one DB transaction.
   - Separate outbox relay process/thread reads unpublished outbox rows and publishes to Kafka, marking them published only after broker ack.
   - Guarantees: DB commit implies the event *will* eventually reach Kafka (at-least-once), never "commit succeeded, event lost forever."

3. **Worker process**
   - Separate process (not the API process) consumes `job.queued` topic.
   - On receipt: atomically claims the job (conditional UPDATE, `WHERE status = 'QUEUED'`), executes, transitions to terminal state.
   - Multiple worker replicas may run concurrently; only one may claim a given job.

4. **Idempotent execution**
   - Every job execution recorded by `(job_id, attempt_id)` in an `executions` table with a unique constraint.
   - Redelivery of the same Kafka message (same job_id) must not re-run business logic if that job is already past `RUNNING` — worker checks current DB status before executing, and execution insert is idempotent via unique constraint.

5. **Job cancellation**
   - `POST /v1/jobs/{id}/cancel` — new endpoint (extends V0.1 API, doesn't break it).
   - Cancels only if job is `PENDING` or `QUEUED` (atomic conditional UPDATE). If already `RUNNING`, marks a `cancel_requested` flag; worker checks flag at safe checkpoints (V0.2 scope: check before execution starts and after; no mid-execution preemption — real preemption is out of scope until execution has actual checkpointable work in V0.6+).

6. **Kafka topics** (minimum)
   - `job.queued` — producer: API/outbox relay. consumer: workers.
   - `job.cancel` — producer: API. consumer: workers (best-effort signal for in-flight jobs).

## Non-functional requirements
- No new orchestration tech: no Kubernetes, Redis, Ray, GPU scheduling, priority/fairness, heartbeats, autoscaling (explicitly deferred to V0.3/V0.4).
- Worker and outbox relay are separate OS processes from the API, each independently restartable, each stateless except for DB.
- All new failure/idempotency logic must be integration-tested against real Postgres + real Kafka (via docker-compose), not mocked — consistent with V0.1's precedent of rejecting DB mocks.

## Explicit non-goals for V0.2
- Priority/fairness scheduling (V0.4)
- Worker heartbeat / stale-worker detection (V0.3, per original roadmap — but since V0.2 already needs "worker crashed after acquiring job" handling, see Known Gap below)
- Kubernetes deployment
- Retry engine / DLQ (V0.3, though a crashed job simply sits `RUNNING` forever until V0.3 adds detection — documented as accepted risk, see failure scenarios)
- GPU-aware scheduling

## Known gap carried forward (accept, document, don't silently fix)
V0.2 introduces "worker crashes after acquiring job" as a failure scenario but the *general* fix (heartbeat + staleness detection + automatic recovery) is explicitly V0.3 scope per the original roadmap. V0.2's answer is: the job is stuck `RUNNING`, detectable manually via a query, not auto-recovered. This is acceptable for V0.2 and must be called out in the README, not hidden.

## Acceptance criteria (must all pass before v0.2.0 tag)
- [ ] State machine enforced: illegal transition (e.g. `PENDING -> RUNNING` directly, or `SUCCEEDED -> QUEUED`) rejected with 409, verified by test
- [ ] Transactional outbox: integration test proves job creation and outbox row are atomic (rollback one, other doesn't persist)
- [ ] Outbox relay test: kill Kafka after DB commit, confirm event publishes once Kafka recovers (no data loss, no duplicate outbox publish)
- [ ] Duplicate Kafka delivery test: manually redeliver same message twice, confirm job executes exactly once (via `executions` unique constraint)
- [ ] Concurrent worker test: 2+ worker processes racing on the same message, confirm only one claims the job (conditional UPDATE proven via test asserting rowcount)
- [ ] Worker crash test: kill worker mid-execution, confirm job remains `RUNNING` (not silently lost, not double-run when a second worker consumer restarts and doesn't re-claim an already-`RUNNING` job)
- [ ] Cancellation test: cancel `QUEUED` job succeeds atomically; cancel `RUNNING` job sets flag, worker observes it at next checkpoint
- [ ] Kafka-down test: API/outbox relay continues accepting job creation (outbox buffers), publishes once Kafka returns
- [ ] Postgres-down test: system fails closed (matches V0.1 `/readyz` behavior, no worker executes without DB)
- [ ] Clean-room docker-compose verification: `docker compose down -v && up --build` brings up API + worker + Kafka + Postgres, full async job lifecycle (create -> queued -> running -> succeeded) observed end-to-end via curl + logs
- [ ] All new numbers/claims trace to actual test/run output (same rule as V0.1)
