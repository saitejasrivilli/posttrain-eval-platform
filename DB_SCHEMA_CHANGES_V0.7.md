# DB SCHEMA CHANGES — V0.7

V0.7 adds evaluation state without modifying the V0.5/V0.6 immutable training records.

## Tables

### evaluation_configs
- id UUID PK
- task_type
- metric_definitions JSONB
- batch_size
- max_examples
- max_sequence_length
- evaluator_code_commit
- container_image
- random_seed
- created_at

### evaluation_runs
- id UUID PK
- model_id
- model_version_number
- dataset_id
- dataset_version_number
- evaluation_config_id FK
- job_id FK
- baseline_model_id nullable
- baseline_model_version_number nullable
- status
- evaluator_code_commit
- container_image
- created_at
- completed_at nullable

Foreign keys preserve the fixed lineage model. Candidate and baseline references are immutable.

### evaluation_results
- evaluation_run_id FK
- example_id
- prediction JSONB/text
- expected_output JSONB/text
- score nullable
- latency_ms nullable
- error_code nullable
- error_message nullable
- attempt_number
- created_at

Primary/unique identity should be `(evaluation_run_id, example_id)`.

### evaluation_metrics
- evaluation_run_id FK
- metric_name
- metric_value
- split
- sample_count
- created_at

Unique identity: `(evaluation_run_id, metric_name, split)`.

### quality_gates
- id UUID PK
- name
- rules JSONB
- created_at

### quality_gate_results
- id UUID PK
- evaluation_run_id FK
- quality_gate_id FK
- status
- rule_results JSONB
- evaluated_at

Unique identity: `(evaluation_run_id, quality_gate_id)`.

## Migration rules
- All new tables created through Alembic.
- No destructive migration of existing V0.1-V0.6 tables.
- Existing TrainingRun, ModelVersion, DatasetVersion, Job, Attempt, Artifact rows remain immutable.
- Fresh database must apply all migrations in order.
