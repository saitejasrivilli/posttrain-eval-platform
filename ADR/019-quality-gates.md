# ADR 019 — Quality Gates

## Context
Evaluation produces measurements, but a platform needs an explicit policy boundary to decide whether measurements satisfy a release criterion.

## Decision
Quality gates are immutable declarative policies evaluated only against persisted evaluation metrics. Gate results are durable and have PASS, FAIL, or ERROR semantics.

The gate system does not register a model or promote it automatically.

## Why
Separating measurement from policy prevents evaluator code from embedding deployment policy and makes the decision auditable and reproducible.

## Consequences
- PASS/FAIL decisions can be reproduced from stored metrics and gate rules.
- Missing or incompatible evidence produces ERROR, never an accidental PASS.
- Promotion remains an explicit future/control-plane action.
