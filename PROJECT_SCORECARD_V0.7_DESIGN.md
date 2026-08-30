# V0.7 DESIGN SCORECARD

Status: **DESIGN COMPLETE — implementation not started**

| Capability | Design status | Evidence required before release |
|---|---|---|
| Immutable EvaluationConfig | Designed | migration + immutability tests |
| Immutable EvaluationRun | Designed | migration + state/immutability tests |
| Exact ModelVersion/DatasetVersion inputs | Designed | integration test + live run |
| Real evaluator subprocess | Designed | real T4 evaluation |
| Per-example results | Designed | integration + duplicate tests |
| Aggregate metrics | Designed | deterministic metric tests |
| Baseline comparison | Designed | compatibility/race tests |
| Quality gates | Designed | PASS/FAIL/ERROR tests |
| Evaluator fencing | Designed | release-blocking stale-write tests |
| Crash recovery/retry | Designed | real worker/evaluator crash test |
| Resource scheduling | Reused | existing V0.4 evidence + regression suite |
| Artifact/lineage preservation | Reused | existing V0.5/V0.6 regression suite + live lineage |
| Cancellation races | Designed | dedicated race tests |
| Clean-room migration | Designed | fresh DB Docker run |
| End-to-end evaluation | Designed | ModelVersion -> evaluation -> metrics -> gate live proof |

## Explicitly not claimed
- No implementation yet.
- No real evaluation result yet.
- No quality-gate PASS/FAIL evidence yet.
- No production promotion workflow.
- No LLM-as-a-judge.
- No distributed evaluation.
