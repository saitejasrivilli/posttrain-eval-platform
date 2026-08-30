# EVALUATION MODEL — V0.7

## EvaluationConfig
Immutable declaration of how an evaluation is performed.

Core fields:
- id
- task_type
- metric_definitions
- batch_size
- max_examples
- max_sequence_length
- evaluator_code_commit
- container_image
- random_seed
- created_at

No UPDATE path after creation.

## EvaluationRun
Immutable record of one evaluation request.

Fields:
- id
- model_id
- model_version_number
- dataset_id
- dataset_version_number
- evaluation_config_id
- job_id
- status
- evaluator_code_commit
- container_image
- created_at
- completed_at

The referenced model, dataset, and config identities cannot change after creation.

## EvaluationResult
One durable result per evaluation example.

Fields:
- evaluation_run_id
- example_id
- prediction
- expected_output
- score
- latency_ms
- error_code
- error_message
- attempt_number
- created_at

Uniqueness should prevent duplicate logical results for the same `(evaluation_run_id, example_id)` while allowing a retried attempt to produce the same logical result only once.

## EvaluationMetric
Aggregate metric values.

Fields:
- evaluation_run_id
- metric_name
- metric_value
- split
- sample_count
- created_at

Metric rows are immutable once the EvaluationRun reaches terminal success.

## QualityGate
A policy applied to stored metrics.

Fields:
- id
- name
- rules
- created_at

Rules are declarative, for example:
```json
{
  "all": [
    {"metric": "exact_match", "operator": ">=", "value": 0.80},
    {"metric": "latency_p95_ms", "operator": "<=", "value": 500}
  ]
}
```

## QualityGateResult
Durable evaluation of one gate against one EvaluationRun.

Fields:
- id
- evaluation_run_id
- quality_gate_id
- status
- rule_results
- evaluated_at

Possible status values: PASS, FAIL, ERROR.

## Baseline comparison
Baseline is a reference, not a mutable field on the model. An EvaluationRun may optionally identify a baseline ModelVersion. The platform must validate that baseline and candidate use the same DatasetVersion and compatible EvaluationConfig before comparison.

A baseline comparison produces explicit delta metrics; it does not rewrite either model or dataset metadata.
