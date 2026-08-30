# ADR 017 — Evaluation Control Plane

## Context
V0.6 produces real post-trained ModelVersions but does not measure their quality. Evaluation must become a durable workload without creating a parallel execution architecture.

## Decision
Use the existing Job/Scheduler/Worker/subprocess infrastructure for evaluation. Add evaluation-specific immutable metadata, per-example results, aggregate metrics, and quality-gate persistence.

## Alternatives rejected
- Separate evaluation microservice: duplicates execution/recovery machinery.
- In-process evaluation: weak failure isolation and conflicts with V0.6 subprocess model.
- Workflow/DAG engine: unnecessary for the fixed V0.7 flow.
- LLM-as-a-judge: introduces another model and evaluation failure mode before the deterministic baseline is established.

## Consequences
Evaluation inherits proven scheduling, lease, fencing, retry, and resource behavior. New result writes must extend fencing rather than bypass it.
