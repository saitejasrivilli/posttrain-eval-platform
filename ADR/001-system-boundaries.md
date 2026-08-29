# ADR 001: System Boundaries for V0.1

## Status
Proposed — pending user review before implementation begins.

## Context
posttrain-eval-platform will grow through 10 versions (V0.1 -> V1.0) into a system with Kafka, Kubernetes, GPU workers, and ML registries. Building all of it upfront risks an unverified, over-engineered skeleton. Need to draw a hard line for what V0.1 contains, and how future versions attach without rework.

## Decision 1: Single deployable service, not microservices
V0.1 is one FastAPI process + one Postgres instance. No separate services for jobs/datasets/eval — those don't exist yet as concepts.
**Tradeoff:** Simpler now, but risks becoming a monolith if internal layering isn't disciplined. Mitigation: strict router -> service -> repository layering from day 1, so extraction later is mechanical, not a rewrite.

## Decision 2: Alembic for migrations (not raw versioned SQL scripts)
**Why:** Python-native, integrates with SQLAlchemy models, auto-generates migration diffs, well-understood by hiring reviewers.
**Tradeoff:** Slightly more setup than raw SQL scripts; worth it for a project with 10 planned schema evolutions ahead.

## Decision 3: No job state machine enforcement yet
`status` field exists and is settable, but V0.1 does not enforce valid transitions (PENDING -> QUEUED -> RUNNING -> ...). That state machine is explicitly a V0.2 deliverable.
**Tradeoff:** Real state machine tested with the job system, not bolted onto a system that doesn't execute anything yet — avoids designing enforcement rules against a hypothetical worker.

## Decision 4: No auth in V0.1
**Why:** Nothing sensitive to protect yet (no artifacts, no execution, no PII by default). Adding auth now means guessing at what V0.2+ actually needs.
**Tradeoff:** Documented gap, not a silent omission. Must not ship past V0.4 without revisiting.

## Decision 5: Control plane / data plane split declared now, data plane not built
The `jobs` table and API are designed so a future worker (data plane) can be added by inserting a queue between service and repository — without renaming fields or breaking the API contract.
**Tradeoff:** Some schema fields (worker_id, attempt_count) are anticipated in docs but NOT added as columns yet — avoids speculative schema no version currently uses.

## Consequences
- V0.1 is intentionally thin. Reviewers should not expect scheduling, queueing, or execution at this stage — those are explicitly out of scope and tracked in later ADRs.
- Every future version gets its own ADR; this one is not amended, only referenced.
