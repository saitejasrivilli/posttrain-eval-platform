# Database Schema Changes — V0.5

Additive only. No V0.1-V0.4 table modified except a new nullable FK column noted below.

## `artifacts` (new)
```
id             UUID PRIMARY KEY
content_hash   TEXT NOT NULL        -- sha256, hex
storage_key    TEXT NOT NULL        -- derived from content_hash, e.g. "sha256/<hash>"
artifact_type  TEXT NOT NULL        -- DATASET | MODEL | CHECKPOINT
size_bytes     BIGINT NULL          -- populated once known (may be null while PENDING)
attempt_number INTEGER NULL         -- populated for training-produced artifacts (LINEAGE_MODEL_V0.5.md)
job_id         UUID NULL REFERENCES jobs(id)   -- which job's attempt produced this, if any
status         TEXT NOT NULL DEFAULT 'PENDING' -- PENDING | UPLOADED | FAILED
uploader_id    TEXT NULL            -- who currently holds the upload lease, if anyone
upload_lease_expires_at TIMESTAMPTZ NULL  -- NULL = never claimed; past = lapsed/abandoned
created_at     TIMESTAMPTZ NOT NULL
uploaded_at    TIMESTAMPTZ NULL
```
Unique index on `(content_hash)` -- content-addressing means the same bytes never get two artifact rows (ADR 011); a second upload of identical content reuses the existing row (see API_CHANGES_V0.5.md's upload semantics).
Partial index `(status) WHERE status='PENDING'` for the Reconciler's sweep query.
`uploader_id`/`upload_lease_expires_at` are the upload-ownership lease (ADR 013, reuses ADR 004's `jobs.lease_owner`/`lease_expires_at` mechanism) -- this is what lets the Reconciler distinguish "actively being uploaded" from "abandoned" instead of guessing from row age alone.

## `datasets` (new)
```
id           UUID PRIMARY KEY
name         TEXT NOT NULL UNIQUE
description  TEXT NULL
created_at   TIMESTAMPTZ NOT NULL
```

## `dataset_versions` (new)
```
dataset_id      UUID NOT NULL REFERENCES datasets(id)
version_number  INTEGER NOT NULL
artifact_id     UUID NOT NULL REFERENCES artifacts(id)
created_at      TIMESTAMPTZ NOT NULL
PRIMARY KEY (dataset_id, version_number)
```
Composite PK, same pattern as `attempts`/`reservations` (ADR 006/DB_SCHEMA_CHANGES_V0.4.md precedent) -- the natural identity of a version is "which dataset, which number."

## `models` (new)
```
id                     UUID PRIMARY KEY
name                   TEXT NOT NULL UNIQUE
description            TEXT NULL
created_at             TIMESTAMPTZ NOT NULL
```

## `model_versions` (new)
```
model_id             UUID NOT NULL REFERENCES models(id)
version_number       INTEGER NOT NULL
artifact_id          UUID NOT NULL REFERENCES artifacts(id)
training_run_id      UUID NULL REFERENCES training_runs(id)  -- nullable: a model version
                                                               -- could in principle be
                                                               -- registered from an artifact
                                                               -- not produced by this platform's
                                                               -- own training runs (e.g. an
                                                               -- imported base model)
registered_at        TIMESTAMPTZ NOT NULL   -- when THIS explicit registration happened,
                                             -- distinct from the artifact's own uploaded_at
PRIMARY KEY (model_id, version_number)
```
Application-layer check (not a DB constraint, since it requires reading the referenced artifact's status): `artifact_id` must reference an `UPLOADED` artifact at registration time (ARTIFACT_LIFECYCLE_V0.5.md's hard invariant).

## `training_runs` (new)
```
id                      UUID PRIMARY KEY
job_id                  UUID NOT NULL REFERENCES jobs(id)
dataset_version_id      UUID NOT NULL REFERENCES dataset_versions(dataset_id, version_number)  -- composite FK
base_model_version_id   UUID NULL    -- FK to model_versions(model_id, version_number), nullable
training_config         JSONB NOT NULL
code_commit             TEXT NOT NULL
container_image         TEXT NOT NULL
random_seed             INTEGER NULL
created_at              TIMESTAMPTZ NOT NULL
```
`training_config` stored inline as JSONB rather than a separate `training_configs` table with its own versioning -- a training run's config is captured at the moment of creation and never needs independent versioning/reuse-tracking of its own; if config reuse across runs becomes a real need, a separate table can be introduced later without breaking this column (additive).

## Migration sequencing
1. `0013_create_artifacts.py`
2. `0014_create_datasets_and_versions.py`
3. `0015_create_models_and_versions.py`
4. `0016_create_training_runs.py`

Four migrations, each independently revertible, same discipline as every prior version.
