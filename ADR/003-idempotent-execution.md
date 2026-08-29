# ADR 003: Idempotent Job Execution

## Status
Proposed — pending user review before implementation.

## Context
ADR 002 establishes at-least-once Kafka delivery (outbox relay may republish, Kafka consumer groups may redeliver on rebalance, a worker may reprocess after crash-recovery). Additionally two workers may race on the same message before either commits a claim. Required failure scenarios: "duplicate Kafka delivery" and "two workers receive the same job" must both result in exactly one real execution.

## Decision
Two layered mechanisms, not one, because they defend against different failure classes:

1. **Claim-level concurrency control (defends against two workers racing on one message):**
   `UPDATE jobs SET status='RUNNING' WHERE id=:id AND status='QUEUED'`, executed as a single atomic statement. Postgres row-level locking guarantees exactly one concurrent UPDATE succeeds (rowcount 1); all others get rowcount 0 and no-op. No application-level locking, no distributed lock service — the database's own atomicity is sufficient at this scale.

2. **Execution-record idempotency (defends against duplicate delivery of an already-completed job):**
   Every execution attempt inserts a row into `executions` with a unique constraint on `(job_id, attempt_id)`. Before executing, the worker reads the job's current status: if it's already terminal (`SUCCEEDED`/`FAILED`/`CANCELLED`), the worker acks the message and does nothing further. This check is what actually prevents redelivery from re-running side effects after a worker has already fully processed a job and moved on (the claim UPDATE alone doesn't help here, since by the time a duplicate message arrives the job is no longer `QUEUED` — it's already terminal, so the UPDATE naturally no-ops too, but the explicit status read makes the "no work to do" path an intentional no-op rather than an accidental one, and gives us something to assert in tests).

## Why `attempt_id = job_id` in V0.2 specifically
V0.2 has no retry engine (that's V0.3). There is exactly one execution attempt per job in this version's model. So the theoretically general `(job_id, attempt_id)` key collapses to `(job_id, job_id)` — i.e., a unique constraint on `job_id` alone in the `executions` table. This is deliberately not over-built with a fake "attempt_id: int, always 1" column; the schema documents this collapse explicitly (see schema section) so V0.3 can extend it (add a real incrementing attempt counter) without a breaking migration — just relaxing the uniqueness scope.

## Alternatives considered
- **Idempotency purely via Kafka consumer offset commits (commit only after full success):** doesn't defend against the two-workers-race scenario, and doesn't survive worker crash between execution and offset commit (message would redeliver into a system with no record it already ran, except the job's status — which is exactly what we already check, so this reduces to the same mechanism without adding anything).
- **Distributed lock (e.g. Redis-based):** explicitly out of scope — Redis not permitted in V0.2, and unnecessary since Postgres row locking already solves the claim race.
- **Exactly-once Kafka semantics (transactional producer/consumer):** adds meaningful operational complexity for a guarantee we don't need — at-least-once delivery + idempotent consumer is a strictly simpler, well-understood pattern that achieves the same observable outcome (exactly-once *effect*, not exactly-once *delivery*).

## Explicit migration path (invariant, not just a note)
```
V0.2:  job_id -> exactly one logical execution record   (executions.job_id PRIMARY KEY)
V0.3+: job_id -> attempt_1
              -> attempt_2
              -> attempt_3                                (executions.(job_id, attempt_id) PRIMARY KEY)
```
V0.2 code and schema must not bake in "one execution per job forever" as a business rule anywhere outside this single PK constraint — the collapse is a V0.2-scope simplification, not a modeling decision that V0.3 has to work around. When V0.3 adds retries, the migration adds an `attempt_id` column, backfills existing rows with `attempt_id = 1`, and changes the primary key to the composite — additive, not a redesign.

## Consequences
- Every job execution must be safe to no-op-check before doing real work — this constrains future executor implementations (V0.6 SFT/DPO workers) to always check status first, a rule that must be documented for whoever builds those, not just assumed.
- The `executions` table is small overhead (one row per job) for a strong, testable guarantee.
