# ADR 002: Transactional Outbox for Job Event Publishing

## Status
Proposed — pending user review before implementation.

## Context
V0.2 needs the API to both (a) persist a job in Postgres and (b) notify Kafka so a worker can pick it up. These are two different systems with no shared transaction. Writing to both naively (the "dual write" problem) creates two failure windows:
- DB commit succeeds, Kafka publish fails/crashes before send -> job exists but no worker will ever see it (silently stuck `QUEUED` forever).
- Kafka publish succeeds, DB commit fails/rolls back -> worker receives a message for a job that doesn't exist.

Given the required failure scenario "DB commit succeeds but Kafka publish fails" must not lose the job, dual-write is unacceptable.

## Decision
Use the transactional outbox pattern:
1. Job insert and an `outbox` row insert happen in the same Postgres transaction. Either both persist or neither does — no window where one exists without the other.
2. A separate **Outbox Relay** process polls `outbox WHERE published_at IS NULL`, publishes each to Kafka, and marks `published_at` only after broker ack.
3. If the relay crashes mid-publish, the unpublished row is still there on restart — it republishes. This makes publishing **at-least-once**, never zero-times.

## Alternatives considered
- **Change Data Capture (Debezium) reading the Postgres WAL:** more robust (no polling lag, no relay-process-as-SPOF pattern) but adds a new piece of infrastructure (Debezium + Kafka Connect) disproportionate to V0.2's scope. Revisit if polling latency becomes a real measured problem.
- **Publish directly after commit, best-effort, ignore failures:** rejected outright — directly violates the required "DB commit succeeds but Kafka publish fails" failure scenario; this is the dual-write bug, not a design.
- **2PC / distributed transaction across Postgres and Kafka:** Kafka doesn't support XA/2PC in a way that's practical here; rejected as over-engineering for this scope.

## Delivery contract (precise, not just "outbox exists")
The relay itself is at-least-once, not exactly-once: it may publish to Kafka, then crash before marking the outbox row `published_at`, then republish the same event on restart. This is a real, expected sequence, not a residual risk:
```
Relay -> publish -> Kafka accepts -> Relay crashes before marking published
       -> Relay restarts -> republishes same event
```
The layered contract is:
```
Postgres = source of truth
Outbox   = durable intent to publish
Kafka    = transport (at-least-once, no ordering guarantee relied upon)
Consumer = idempotent (ADR 003)
```
V0.2 never claims exactly-once end-to-end. It claims exactly-once *effect* via idempotent consumption of an at-least-once pipe — those are different guarantees and only the second is true here.

## Consequences
- At-least-once delivery means duplicate messages are a normal, expected occurrence, not an edge case — this is precisely why idempotent execution (ADR 003) is mandatory, not optional hardening.
- The Outbox Relay is a new single point of publishing latency (polling interval, default e.g. 500ms) — acceptable for V0.2's scope (no latency SLO exists yet; will be measured, not guessed, if a later version needs one).
- `outbox` table grows unless pruned — pruning/retention policy is out of scope for V0.2 (single small table, manual cleanup acceptable at this scale); revisit if V1.0 load testing shows it matters.
