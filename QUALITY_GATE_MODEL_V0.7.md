# QUALITY GATE MODEL — V0.7

## Rule model
A gate contains explicit rules over named aggregate metrics. Rules must be deterministic and type-checked.

Supported initial operators:
- `>=`
- `>`
- `<=`
- `<`
- `==`

Logical composition:
- `all`
- `any`

Example:
```json
{
  "all": [
    {"metric": "exact_match", "operator": ">=", "value": 0.80},
    {"metric": "latency_p95_ms", "operator": "<=", "value": 500}
  ]
}
```

## Baseline rules
A baseline rule may compare a candidate metric against a baseline metric:
```json
{
  "metric": "exact_match",
  "operator": ">=",
  "baseline_delta": 0.02
}
```

Baseline comparisons are valid only when candidate and baseline use the same DatasetVersion and compatible EvaluationConfig. The evaluator must reject incompatible comparisons instead of silently normalizing them.

## Decision semantics
- PASS: every required rule passed.
- FAIL: at least one required rule failed.
- ERROR: evaluation could not be performed reliably because a required metric is missing, invalid, or incompatible.

`ERROR` is never converted to PASS.

## Separation of concerns
Evaluation measures. Quality gates apply policy. Model registration/promotion remains an explicit API action outside the gate evaluator.
