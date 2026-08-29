# Release Notes — v0.2.0

Durable asynchronous job execution: Kafka(Redpanda)-transported, PostgreSQL-sourced-of-truth, idempotent, enforced state machine.

## Summary
31/31 tests passing. Clean-room Docker verification (`docker compose down -v && up --build`) against real Redpanda and real PostgreSQL — no mocks in any distributed-failure test.

## Verified (live, against real infrastructure)
- Worker crash before Kafka offset acknowledgement -> redelivery at the same offset -> idempotent no-op -> exactly one execution row, correct terminal state
- Outbox relay crash after real Kafka publish ack, before marking the row delivered -> relay restart republished the event (two distinct Kafka offsets, same job) -> worker: first delivery claimed and executed, duplicate delivery was a no-op -> exactly one execution row
- Concurrent workers racing the same job (5 threads, real Postgres) -> exactly one claim succeeds
- Cooperative cancellation: immediate for `QUEUED`, flag-based checkpoint for `RUNNING`, rejected with 409 for terminal jobs
- Invalid state transitions (e.g. `CANCELLED -> RUNNING`) rejected with 409
- Kafka unavailable -> job creation still succeeds (outbox buffers), publishes once Kafka recovers, zero data loss
- PostgreSQL unavailable -> system fails closed (`/readyz` 503, job creation 500), clean recovery after restart

## Guarantee, stated precisely
Transport is **at-least-once**, not exactly-once. Effectively-once logical execution is achieved through conditional-UPDATE atomic claiming plus execution-record idempotency — not through any Kafka delivery guarantee. This was proven, not assumed: both the worker-ack crash window and the relay-publish crash window were exercised against real broker and real database, with duplicate deliveries confirmed to reach the idempotency check and correctly no-op.

## Known limitation (by design, not a bug)
A `RUNNING` job whose worker crashes without transitioning it to a terminal state stays `RUNNING` indefinitely. V0.2 has no heartbeat, lease, or stale-job detection — that's V0.3. Proven via test that no code path in V0.2 auto-recovers such a job.

## Explicitly out of scope for V0.2
Kubernetes, Redis, Ray, GPU scheduling, priority/fairness scheduling, worker heartbeats, stale-job reaper, retry engine, DLQ, authentication, multi-tenancy.
