# API Changes — V0.5

All V0.1-V0.4 endpoints unchanged. V0.5 adds new resource families entirely; nothing existing is modified.

## Datasets
- `POST /v1/datasets` -- create a `Dataset` (name, description).
- `POST /v1/datasets/{id}/versions` -- upload content, creates an artifact (PENDING->UPLOADED synchronously for V0.5's expected sizes) and a `DatasetVersion` (DATASET_MODEL_V0.5.md). Returns 201 with the version, or an error if upload never reaches `UPLOADED` (no partial `DatasetVersion` ever created).
- `GET /v1/datasets/{id}/versions` -- list, paginated (same `limit`/`offset` convention as every prior list endpoint).
- `GET /v1/datasets/{id}/versions/{version_number}` -- one version's metadata + a storage reference (not inline bytes).

## Models
- `POST /v1/models` -- create a `Model` (name, description).
- `POST /v1/models/{id}/versions` -- **explicit registration**, body references an existing `UPLOADED` `artifact_id` (and optionally `training_run_id`). Rejects (409) if the artifact isn't `UPLOADED`, or if the artifact is already registered as a different model version (duplicate registration -- see FAILURE_SCENARIOS_V0.5.md).
- `GET /v1/models/{id}/versions` -- list.
- `GET /v1/models/{id}/versions/{version_number}/lineage` -- the full lineage query (LINEAGE_MODEL_V0.5.md), the primary new capability this version exists to deliver.

## Training Runs
- `POST /v1/training-runs` -- body: `dataset_version_id`, `base_model_version_id` (optional), `training_config`, `code_commit`, `container_image`, `random_seed` (optional), plus whatever `job_type`/`config` the underlying job needs (V0.2). Creates a `training_runs` row AND a `jobs` row (via the existing, unmodified V0.2 job-creation path) in one request -- the job then proceeds through the existing pipeline untouched.
- `GET /v1/training-runs/{id}` -- training run metadata + current job/attempt status (joins to the existing `jobs`/`attempts` tables, read-only, no new job-status concept).

## Artifacts (read-only, debugging/observability)
- `GET /v1/artifacts/{id}` -- status, content_hash, size, type, uploaded_at.
- `GET /v1/artifacts?status=PENDING` -- primarily for operators inspecting stuck uploads (same role as V0.3's DLQ endpoint).

## No changes
Every V0.1-V0.4 endpoint's contract is unchanged.
