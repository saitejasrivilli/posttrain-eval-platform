# State Transition Documentation — V0.5

V0.5 introduces two independent state machines, neither of which modifies the existing V0.2/V0.3 job state machine.

## 1. Artifact lifecycle (see ARTIFACT_LIFECYCLE_V0.5.md for full detail)
```
PENDING -> UPLOADED   (upload succeeds + hash verified, OR Reconciler self-heal)
PENDING -> FAILED     (Reconciler, grace period elapsed, bytes absent)
```
Both `UPLOADED` and `FAILED` are terminal.

## 2. ModelVersion registration is NOT a state machine on the training run
This is the most important modeling decision in V0.5, restated precisely: there is no `training_runs.status` value like `MODEL_REGISTERED`. Registration is the creation of a *separate* row (`model_versions`), not a transition of the `TrainingRun`'s own state. A `TrainingRun` has exactly one lifecycle, inherited entirely from its underlying `jobs.status` (V0.2/V0.3, unmodified) -- `QUEUED -> RUNNING -> SUCCEEDED/FAILED/CANCELLED`, with retries per V0.3. Once `SUCCEEDED` (and an artifact exists, `UPLOADED`), the `TrainingRun` itself has nothing further to transition through. "Was this registered as a model version" is answered by *querying whether a `model_versions` row exists referencing this training run's artifact*, not by a status field.

**Why this matters concretely:** it makes "don't automatically register every successful training output as a production model" a structural fact, not a policy someone could accidentally violate by adding an auto-transition later -- there is no state for "registered" to transition into on the training run itself; registration only ever happens via the explicit `POST /v1/models/{id}/versions` endpoint creating a new, independent row.

## 3. TrainingRun immutability (required clarification)
A `TrainingRun` row is fully immutable once created -- there is no update endpoint, and no application code path updates any of its columns after the INSERT. Precisely: `dataset_version_id`, `base_model_version_id`, `training_config`, `code_commit`, `container_image`, `random_seed`, and `job_id` are all fixed forever at creation time. This matters specifically because these are exactly the fields that answer "how was this model produced" (LINEAGE_MODEL_V0.5.md) -- if any of them could change after execution started, lineage would be lying about what actually happened.

This is the same *kind* of guarantee ADR 010 gives `DatasetVersion` (no update path exists, not "please don't update it") applied to `TrainingRun`. There are no "operational fields" on `TrainingRun` that need to change post-creation -- its current execution status is entirely derived by joining to `jobs`/`attempts` (V0.2/V0.3, unmodified), never stored redundantly on the `TrainingRun` row itself. Storing a redundant status field on `TrainingRun` would create exactly the kind of two-places-to-keep-in-sync problem this project has avoided since V0.2's `attempt_number` design (ADR 006) -- so it's deliberately not done.

## Relationship to job cancellation (V0.2/V0.3, unmodified)
If a `TrainingRun`'s underlying job is cancelled, the job's own state machine handles it exactly as before (`RUNNING -> CANCELLED` cooperative, or `QUEUED -> CANCELLED` immediate) -- no new cancellation logic for `TrainingRun` itself. The consequence for lineage: a cancelled `TrainingRun` simply has no `UPLOADED` artifact (or an orphaned `PENDING` one that reconciles to `FAILED`), so it can never be registered as a `ModelVersion` -- enforced by the artifact-status invariant (ARTIFACT_LIFECYCLE_V0.5.md), not by any cancellation-specific check.

## Relationship to job retry (V0.3, unmodified)
A `TrainingRun` whose job retries (transient failure, V0.3) is still the *same* `TrainingRun` row -- retries are an execution-engine concern (attempt_number advancing within one job), not a new training run. If a later attempt succeeds and produces an artifact, that artifact records which `attempt_number` produced it (LINEAGE_MODEL_V0.5.md). If an *earlier* failed attempt also happened to produce a (partial or invalid) artifact before failing, that artifact would independently reconcile to `FAILED` or sit unreferenced -- it does not retroactively get linked to the training run's final artifact_id unless it's the one that actually succeeded.
