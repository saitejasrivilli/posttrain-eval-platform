# API CHANGES — V0.7

## Evaluation config
`POST /v1/evaluation-configs`

Creates an immutable evaluation configuration.

`GET /v1/evaluation-configs/{id}`

## Evaluation runs
`POST /v1/evaluations`

Creates an EvaluationRun for an existing ModelVersion and DatasetVersion.

Request includes:
- model_id / model_version
- dataset_id / dataset_version
- evaluation_config_id
- optional baseline_model_id / baseline_model_version

Validation rejects missing/non-UPLOADED model artifacts and invalid dataset/config references.

`GET /v1/evaluations/{id}`

Returns status and immutable input identities.

`GET /v1/evaluations/{id}/metrics`

Returns aggregate metrics.

`GET /v1/evaluations/{id}/results`

Returns paginated per-example results.

`GET /v1/evaluations/{id}/quality-gates`

Returns durable gate decisions and individual rule outcomes.

## Quality gates
`POST /v1/quality-gates`

Creates an immutable gate policy.

`POST /v1/evaluations/{id}/quality-gates/{gate_id}/evaluate`

Evaluates a gate against already-persisted metrics. It is a policy operation, not model registration or promotion.

## API guarantees
- Existing V0.1-V0.6 APIs remain unchanged.
- No API mutates ModelVersion, DatasetVersion, TrainingRun, or EvaluationConfig.
- Pagination follows the existing bounded limit/offset convention.
- Invalid state/policy operations return explicit 4xx responses rather than being silently accepted.
