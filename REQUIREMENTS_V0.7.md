# REQUIREMENTS — V0.7 Evaluation & Quality Gates

## Objective
Add a reproducible evaluation control plane on top of V0.1-V0.6. A completed ModelVersion can be evaluated against an immutable DatasetVersion using an immutable EvaluationConfig. Results are durable, queryable, lineage-aware, and can be checked against explicit QualityGate policies.

V0.7 does not replace the V0.3 execution/recovery model, V0.4 scheduler, V0.5 lineage/artifact model, or V0.6 subprocess model. It reuses them.

## Scope
- Single-process evaluator subprocess supervised by the existing Worker.
- One model version evaluated against one immutable dataset version per EvaluationRun.
- Deterministic evaluation configuration and evaluator code version.
- Structured aggregate metrics and per-example results.
- Baseline comparison and threshold-based quality gates.
- Explicit PASS/FAIL gate decision; no automatic model registration or production promotion.
- Existing leases, fencing, retries, DLQ, scheduling, artifacts, and lineage remain authoritative.

## Initial metrics
V0.7 should implement a deliberately small metric set suitable for the real V0.6 text workload:
- exact_match
- token_accuracy (or equivalent deterministic token-level metric)
- mean_loss / perplexity only when the evaluation task supports labels needed to calculate it
- latency_ms at per-example level and p50/p95 aggregate latency

The implementation must not claim that these metrics are universally meaningful for arbitrary LLM tasks. EvaluationConfig declares the evaluator/task contract.

## Required capabilities
1. Create immutable EvaluationConfig.
2. Create immutable EvaluationRun referencing ModelVersion, DatasetVersion, and EvaluationConfig.
3. Schedule evaluation through the existing job/scheduler path.
4. Load the exact registered model artifact selected by ModelVersion.
5. Load the exact DatasetVersion artifact.
6. Execute evaluation in a supervised subprocess.
7. Record per-example result and error information.
8. Record aggregate metrics with deterministic metric definitions.
9. Capture evaluator code/config/model/dataset identities for reproducibility.
10. Support optional baseline ModelVersion comparison using the same DatasetVersion and compatible evaluation config.
11. Evaluate explicit QualityGate rules after metrics are complete.
12. Store PASS/FAIL decision and individual rule outcomes durably.
13. Ensure stale/fenced evaluators cannot write results after losing ownership.
14. Ensure evaluator crash is recoverable through existing lease/retry machinery.
15. Ensure duplicate delivery does not create duplicate logical evaluation output.
16. Preserve V0.5/V0.6 lineage; evaluation becomes a downstream consumer of ModelVersion rather than modifying training lineage.

## Non-goals
- Automatic promotion to production.
- Online serving or inference autoscaling.
- Human preference evaluation.
- LLM-as-a-judge in the first implementation.
- Distributed evaluation.
- Multi-node execution.
- Hyperparameter search.
- Automatic dataset splitting or mutation.
- Automatic model registration.

## Acceptance criteria
- [ ] EvaluationRun is immutable after creation.
- [ ] EvaluationConfig is immutable after creation.
- [ ] Evaluation uses the exact ModelVersion artifact and DatasetVersion artifact identified by IDs/versions.
- [ ] At least one real evaluation executes on the V0.6-produced model on the T4 environment used for V0.6 evidence.
- [ ] Aggregate metrics and per-example results are queryable through API endpoints.
- [ ] Re-running the same immutable inputs/config produces the same deterministic metric values within explicitly documented floating-point tolerance.
- [ ] Baseline comparison is explicit and cannot silently compare different datasets/configs.
- [ ] Quality-gate rules produce deterministic PASS/FAIL outcomes from stored metrics.
- [ ] Evaluator crash after claim is recovered by V0.3 Recovery and retried.
- [ ] A stale evaluator cannot insert/modify results after fencing/reclamation.
- [ ] Duplicate evaluator delivery is a no-op and does not create duplicate logical results.
- [ ] Cancellation during evaluation follows existing cooperative cancellation semantics.
- [ ] PostgreSQL/object-storage/Kafka failure behavior remains fail-closed and inherits existing mechanisms.
- [ ] Clean-room Docker verification passes with all migrations from scratch.
- [ ] Live end-to-end path is proven: ModelVersion -> EvaluationRun -> scheduled job -> evaluator -> metrics/results -> quality gate.
- [ ] No exactly-once transport claim is introduced.

## Evidence policy
Training quality is not inferred from loss curves alone. Every performance number in release documentation must be copied from an actual evaluation run and tied to model version, dataset version, evaluation config, and evaluator code version.