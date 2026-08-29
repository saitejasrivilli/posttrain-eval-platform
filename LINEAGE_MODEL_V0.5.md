# Lineage Model — V0.5

Full rationale in ADR 012 (fixed FK chain, not a generic graph). This document is the concrete query shape.

## The "how was Model vN produced" query
```
ModelVersion (id, model_id, version_number, artifact_id, training_run_id, registered_at)
  |
  +-- artifact_id       -> Artifact (content_hash, storage_key, size_bytes, uploaded_at)
  |
  +-- training_run_id   -> TrainingRun
                             |
                             +-- dataset_version_id     -> DatasetVersion -> Dataset (name)
                             +-- base_model_version_id  -> ModelVersion (nullable, recursive
                                                            -- this model's own ancestor)
                             +-- job_id                 -> Job (V0.2) -> Attempts (V0.3)
                             +-- training_config (JSON, inline -- see DB_SCHEMA_CHANGES_V0.5.md)
                             +-- code_commit (string)
                             +-- container_image (string)
                             +-- random_seed (int)
```

## Example output shape (matches the target from the review)
```
Model v17
├── dataset: customer_data v42        (join: training_runs.dataset_version_id -> dataset_versions -> datasets)
├── dataset content hash: abc...      (join: dataset_versions.artifact_id -> artifacts.content_hash)
├── code commit: 91fe...              (training_runs.code_commit)
├── config: {learning_rate: ..., ...} (training_runs.training_config)
├── base model: model v12             (training_runs.base_model_version_id -> model_versions)
├── training job: job-123             (training_runs.job_id)
├── attempt: 3                        (jobs.attempt_number at the time SUCCEEDED, or the specific
                                        attempts row that produced this artifact -- see below)
├── artifact: sha256:...              (model_versions.artifact_id -> artifacts.storage_key)
└── evaluations: (empty until V0.7)
```

## Which attempt produced the artifact (retries and lineage)
A `TrainingRun`'s `job_id` may go through several attempts (V0.3) before one succeeds and produces an artifact -- or several *different* attempts could each produce an artifact if a job is somehow re-run (not the normal retry path, which only produces one terminal artifact per run, but worth being precise about). `artifacts.attempt_number` (nullable, populated for training-produced artifacts) records exactly which attempt produced this specific artifact -- so lineage is precise down to "attempt 3 of job-123," not just "job-123, some attempt." This mirrors V0.3's `attempts` table granularity exactly; no new numbering scheme invented.

## What "full lineage" does NOT include (by design, ADR 012)
- No recursive graph traversal beyond the fixed chain above -- `base_model_version_id` only recurses one level per query naturally (a model version's base model version), and querying further ancestry (base model's own base model) is simply another join, not a graph algorithm.
- No lineage for artifacts that were never registered as a `ModelVersion` -- an `UPLOADED` artifact from a training run that was never explicitly registered has a `TrainingRun` record (queryable independently) but doesn't appear in any "model lineage" query, consistent with "training completion != model registration."
