# ARCHITECTURE — V0.2 (updated HLD)

Supersedes nothing in ARCHITECTURE.md's V0.1 description of the control plane — this document only adds the V0.2 delta. V0.1's router->service->repository layering is preserved and reused, not replaced.

## Component diagram

```
                 Client (curl/tests)
                        |
                        v
                 FastAPI app  (unchanged process from V0.1, extended)
                    |      \
                    v       v
                Service   Service (cancel)
                    |
                    v
              Repository  ---------------------------+
                    |                                 |
                    v                                 |
              PostgreSQL                              |
              (jobs, outbox, executions tables)        |
                    ^                                 |
                    |                                 |
            +-------+-------+                         |
            |  Outbox Relay | (new process)            |
            | polls outbox, |                          |
            | publishes to  |                          |
            | Kafka, marks  |                          |
            | rows published|                          |
            +-------+-------+                         |
                    |                                 |
                    v                                 |
                 Kafka                                |
              (job.queued, job.cancel topics)          |
                    |                                 |
                    v                                 |
             +--------------+                          |
             | Worker (N)   |  <------------------------+
             | consumes,    |  (claims job via
             | claims job,  |   conditional UPDATE
             | executes,    |   directly against Postgres)
             | transitions  |
             +--------------+
```

Three OS processes now, up from one in V0.1: **API**, **Outbox Relay**, **Worker** (worker replicable, N>=1). All three talk to Postgres directly through the same repository module (imported as a shared library, not a network service) — no new service boundary invented, consistent with V0.1's "single deployable domain logic" decision, just multiple entrypoints into it.

## Database ownership (updated)
- `jobs` — owned by API (writer for create/cancel), Worker (writer for status transitions during execution), Outbox Relay (reads job data for event payload only).
- `outbox` (new) — written by API in the same transaction as job creation; read + updated (marked published) only by Outbox Relay.
- `executions` (new) — written only by Worker, unique constraint enforces idempotency.

No cross-process writes to tables outside this ownership map — API never writes `executions`, Worker never writes `outbox`.

## Data plane vs control plane (updated)
- **Control plane:** API + Postgres (unchanged from V0.1).
- **Data plane (now exists, was a stub concept in V0.1):** Outbox Relay + Kafka + Worker. This is the seam V0.1's ADR 001 explicitly reserved.

## Job lifecycle -> event flow
1. `POST /v1/jobs` — API inserts `jobs` row (`PENDING`) + `outbox` row, one transaction.
2. API transitions job `PENDING -> QUEUED` in the same transaction (V0.2 has no separate queueing decision yet — that's V0.4's scheduler). Outbox event payload = `{job_id, event: "job.queued"}`.
3. Outbox Relay polls `outbox WHERE published_at IS NULL`, publishes to Kafka topic `job.queued`, sets `published_at` on ack.
4. Worker consumes `job.queued`, attempts `UPDATE jobs SET status='RUNNING' WHERE id=:id AND status='QUEUED'` — rowcount 0 means another worker already claimed it or it was cancelled; worker no-ops and commits the Kafka offset regardless (message considered handled either way).
5. Worker executes job body (V0.2: a no-op/simulated executor — real SFT/DPO training bodies are V0.6, this phase proves the orchestration path only), inserts `executions` row keyed by `(job_id, attempt_id)`, transitions job to `SUCCEEDED`/`FAILED`.
6. Cancellation: `POST /v1/jobs/{id}/cancel` -> conditional UPDATE `WHERE status IN ('PENDING','QUEUED')` sets `CANCELLED` directly (fast path). If job already `RUNNING`, sets `cancel_requested=true` only; worker checks this flag before and after execution and transitions to `CANCELLED` if seen, otherwise proceeds to normal terminal state (last-checkpoint-wins, not preemptive).

## Idempotency mechanism (see ADR 003 for full rationale)
- `executions` table: `(job_id, attempt_id)` unique. Attempt id = deterministic hash of `(job_id, consumer_group_generation_agnostic value)` — V0.2 uses `attempt_id = job_id` itself since V0.2 has no retry engine yet (one execution attempt per job is the only path); the unique constraint therefore degenerates to "one execution per job," which is sufficient for V0.2's actual duplicate-delivery scenario and cheap to extend when V0.3 adds real multi-attempt retries.
- Worker always checks job's current DB status before doing work — a message for an already-`SUCCEEDED`/`FAILED`/`CANCELLED` job is a no-op ack, not an error.

## Kafka as transport only (not source of truth)
- Kafka message loss, duplication, or reordering must never corrupt job state — Postgres row + conditional UPDATE is the single arbiter of "did this job run." This is why the outbox pattern exists (avoids the dual-write problem) and why claims are conditional UPDATEs, not "trust the message."

## Delivery contract (explicit, load-bearing)
```
Postgres = source of truth
Outbox   = durable intent to publish
Kafka    = transport
Consumer = idempotent
```
The Outbox Relay is itself at-least-once (it may publish then crash before marking a row published, then republish on restart — see ADR 002). V0.2 never claims exactly-once delivery end-to-end; it claims exactly-once *effect* through idempotent consumption (ADR 003) of an at-least-once pipe. Every consumer of Kafka messages in this system must be written assuming redelivery is normal, not exceptional.

## Config/observability additions
- New env vars: `KAFKA_BOOTSTRAP_SERVERS`, `WORKER_POLL_INTERVAL_MS` (outbox relay polling cadence — V0.2 uses simple polling, not LISTEN/NOTIFY, to keep scope minimal).
- Structured logs extended with `job_id`, `event_type`, `worker_id` fields on the new processes, same JSON format as V0.1.

## Explicitly NOT introduced in V0.2
Kubernetes, Redis, Ray, GPU scheduling, priority/fairness, heartbeats, autoscaling, retry engine, DLQ, multi-attempt idempotency keys beyond the degenerate case above.
