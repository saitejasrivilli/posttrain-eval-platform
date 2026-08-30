# STATE TRANSITIONS — V0.7

## EvaluationRun
```text
CREATED -> QUEUED -> RUNNING -> SUCCEEDED
                         |          |
                         |          +-> terminal
                         +-> CANCELLED
                         +-> FAILED -> QUEUED (retryable)
                         +-> DLQ (permanent / exhausted)
```

The actual execution lifecycle continues to be governed by the existing Job/Attempt state machine. EvaluationRun mirrors the execution outcome but does not replace Job state.

## QualityGateResult
```text
PENDING -> PASS
        -> FAIL
        -> ERROR
```

A gate is evaluated only from persisted metrics for a completed EvaluationRun. `ERROR` is not treated as PASS.

## Invariants
1. EvaluationRun references cannot change after creation.
2. EvaluationConfig cannot change after creation.
3. ModelVersion and DatasetVersion are read-only inputs.
4. A stale evaluator cannot change EvaluationRun, EvaluationMetric, EvaluationResult, or QualityGateResult.
5. A cancelled run cannot later become successful through a stale completion write.
6. Retry creates a new Job Attempt; it does not mutate immutable evaluation inputs.
7. QualityGateResult is not allowed to promote/register a model automatically.
8. A metric is trusted only if it was written by the currently fenced attempt.
