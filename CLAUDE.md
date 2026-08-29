# Project: posttrain-eval-platform (V0.1 scope)

## What this is
Production-style distributed platform for launching, scheduling, executing, evaluating, and promoting ML/LLM post-training and eval jobs. Built version by version; each version ships to GitHub only after passing its own acceptance criteria.

## Current version: V0.1 — Foundation
Scope (do NOT exceed):
- FastAPI service
- PostgreSQL
- Docker Compose
- DB migrations
- health/readiness endpoints
- basic job metadata API (CRUD, no execution)
- structured logging
- unit + integration tests
- GitHub Actions CI

Explicitly OUT of scope for V0.1 (design for, don't build): Kafka, Kubernetes, Ray, Redis, MinIO, GPU workers, scheduling, auth beyond stub.

## Hard rule
No implementation until REQUIREMENTS.md, ARCHITECTURE.md, ADR/001-system-boundaries.md, PROJECT_SCORECARD.md exist and are reviewed/approved by user. Do not invent architecture ad hoc.

## Future versions (context only, not current scope)
V0.2 job system (Kafka, worker, state machine) -> V0.3 reliability (idempotency, retries, DLQ) -> V0.4 scheduling -> V0.5 ML lifecycle (registries) -> V0.6 post-training (SFT/DPO/GRPO) -> V0.7 evaluation -> V0.8 release mgmt -> V0.9 observability -> V1.0 production simulation + load/failure test reports.

## Non-negotiables (apply at every version)
- No number in any README/doc may be estimated/guessed — must trace to a real measured run, saved under benchmark/results/ (or equivalent) once benchmarks exist.
- Every version gate: design doc -> ADR -> implementation -> unit tests -> integration tests -> failure tests (from V0.3 on) -> perf (from relevant version) -> code review -> docs -> git tag -> GitHub release. No skipping steps.
- Control plane / data plane separation must stay explicit as system grows.
