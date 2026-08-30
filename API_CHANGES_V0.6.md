# API Changes — V0.6

All V0.1-V0.5 endpoints unchanged. V0.6 adds read-only observability endpoints for the new concepts; no existing endpoint's contract changes.

## `TrainingRunCreate` (V0.5, extended)
No new required fields -- `training_config` (already a free-form JSON) now conventionally carries the LoRA/QLoRA hyperparameters (TRAINING_CONFIG_V0.6.md), but the schema itself doesn't change shape.

## New read-only endpoints
- `GET /v1/training-runs/{id}/checkpoints` -- list all registered checkpoints for a run, ordered by step, including which was (if any) selected for resume by the most recent attempt (derivable, not stored redundantly -- computed by re-running the same discovery query, ADR 015).
- `GET /v1/training-runs/{id}/metrics` -- list training metrics (step, loss, learning rate, GPU memory), paginated.
- `GET /v1/training-runs/{id}/output` -- the `training_run_outputs` row, if one exists (404 if the run hasn't completed successfully yet).

## Lineage endpoint extended (V0.5's endpoint, additively)
`GET /v1/models/{id}/versions/{version_number}/lineage` -- the response gains a `checkpoints` list (this run's checkpoint history) and a `training_metrics_summary` (e.g. final loss, total steps) alongside the existing fields (dataset version, training run, job, artifact). No existing field is removed or renamed.

## No changes
Every V0.1-V0.5 endpoint's contract, including `POST /v1/training-runs`, `POST /v1/artifacts`, `POST /v1/models/{id}/versions`, is unchanged. A real training subprocess uses the *same* artifact-upload path (`POST /v1/artifacts` or the internal service call it wraps) that V0.5's simulated test fixtures already exercised -- V0.6 proves that path works for real bytes, it doesn't add a new one.
