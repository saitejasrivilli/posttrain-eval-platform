# Database Schema Changes — V0.6

Additive only. One existing table (`attempts`, V0.3) gains one nullable column; every other V0.1-V0.5 table is unmodified.

## `attempts` (V0.3, extend)
New column:
- `failure_domain` TEXT NULL -- `INFRASTRUCTURE` | `TRAINING`, set alongside `error_classification` on failure (GPU_WORKER_MODEL_V0.6.md's required clarification A). Null for non-failed attempts. Orthogonal to `error_classification` (which answers "retry or not"), this answers "whose fault, for metrics-honesty purposes."

## `checkpoints` (new)
```
training_run_id             UUID NOT NULL REFERENCES training_runs(id)
attempt_number               INTEGER NOT NULL
step                         INTEGER NOT NULL
artifact_id                  UUID NOT NULL REFERENCES artifacts(id)
base_model_id                UUID NULL       -- snapshot, for compatibility rule 4 (ADR 015)
base_model_version_number    INTEGER NULL
checkpoint_format_version    INTEGER NOT NULL
created_at                   TIMESTAMPTZ NOT NULL
PRIMARY KEY (training_run_id, attempt_number, step)
```
Composite PK -- same pattern as `attempts`/`reservations`/`dataset_versions` (ADR 006/010 precedent): the natural identity of a checkpoint is "which run, which attempt, which step." Unique index on `artifact_id` (an artifact is at most one checkpoint, mirroring V0.5's `model_versions.artifact_id` uniqueness).

## `training_metrics` (new)
```
id                UUID PRIMARY KEY
training_run_id    UUID NOT NULL REFERENCES training_runs(id)
attempt_number     INTEGER NOT NULL
step               INTEGER NOT NULL
loss               DOUBLE PRECISION NULL
learning_rate      DOUBLE PRECISION NULL
gpu_memory_allocated_mb INTEGER NULL
recorded_at        TIMESTAMPTZ NOT NULL
```
No composite PK requirement (multiple metric rows can share a step in principle, e.g. eval-loss vs train-loss in a future version) -- a surrogate `id` PK, append-only, same shape as `scheduling_decisions` (V0.4 precedent: an append-only observability log, not a piece of correctness-critical state).

## `training_run_outputs` (new)
```
training_run_id    UUID PRIMARY KEY REFERENCES training_runs(id)
final_artifact_id   UUID NOT NULL REFERENCES artifacts(id) UNIQUE
attempt_number      INTEGER NOT NULL  -- which attempt produced it
created_at          TIMESTAMPTZ NOT NULL
```
`training_run_id PRIMARY KEY` -- exactly one row per training run, ever (STATE_TRANSITIONS_V0.6.md). `final_artifact_id UNIQUE` -- an artifact is the final output of at most one training run.

## `attempt_resume_decisions` (new)
```
training_run_id    UUID NOT NULL REFERENCES training_runs(id)
attempt_number      INTEGER NOT NULL
resumed_from_step   INTEGER NULL   -- NULL means this attempt trained from scratch
decided_at          TIMESTAMPTZ NOT NULL
PRIMARY KEY (training_run_id, attempt_number)
```
Records, once per attempt, the outcome of checkpoint discovery (ADR 015) -- this is what lets a lineage query show "Attempt 2 resumed from checkpoint at step 200" rather than requiring a reader to re-run discovery logic themselves to infer it after the fact. Written by the Worker, fencing-conditioned exactly like `checkpoints`/`training_run_outputs` (ADR 016) -- discovery happens once, at attempt startup, under that attempt's own valid fencing credentials, so no additional race consideration beyond what ADR 016 already covers.

## Migration sequencing
1. `0018_add_failure_domain_to_attempts.py`
2. `0019_create_checkpoints.py`
3. `0020_create_training_metrics.py`
4. `0021_create_training_run_outputs.py`
5. `0022_create_attempt_resume_decisions.py`

Five migrations, each independently revertible, same discipline as every prior version.
