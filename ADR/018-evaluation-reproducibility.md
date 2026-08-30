# ADR 018 — Evaluation Reproducibility

## Context
A metric without immutable input identity is not strong evidence. The platform must explain exactly which model, data, evaluator, and configuration produced a result.

## Decision
Every EvaluationRun records immutable identities for:
- ModelVersion
- DatasetVersion
- EvaluationConfig
- evaluator code commit
- evaluator container image
- random seed where applicable

The evaluation reads the exact registered artifacts referenced by those identities.

## Consequences
Two runs can be compared only when their declared inputs are compatible. Reproducibility is an evidence property, not an assumption that two arbitrary runs are equivalent.
