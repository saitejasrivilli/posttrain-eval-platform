# Database Schema Changes — V0.2

All changes via new Alembic migrations, additive only — no V0.1 columns removed or renamed.

## `jobs` table (extend)
New columns:
- `cancel_requested` BOOLEAN NOT NULL DEFAULT false — set by API on cancel of a `RUNNING` job, read by worker at checkpoints.
- `claimed_at` TIMESTAMPTZ NULL — set when worker successfully claims (`QUEUED -> RUNNING`); useful diagnostic for the "worker crashed after acquiring job" known-gap scenario (lets an operator manually spot stuck jobs by age, even though V0.2 has no automatic detection).

No change to existing columns (`id`, `job_type`, `status`, `config`, `deleted_at`, `created_at`, `updated_at`). `status` remains a string column — V0.2 adds enforcement in code (repository layer), not a DB CHECK constraint, to keep the enforcement logic in one place (Python) rather than split between app and DB. (Revisit if we ever need the DB to be the sole enforcement point for a multi-writer future.)

## `outbox` table (new)
```
id            UUID PRIMARY KEY
job_id        UUID NOT NULL REFERENCES jobs(id)
event_type    TEXT NOT NULL         -- e.g. "job.queued", "job.cancel"
payload       JSONB NOT NULL
created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
published_at  TIMESTAMPTZ NULL      -- NULL = not yet published
```
Index: `(published_at) WHERE published_at IS NULL` — partial index, relay's poll query only scans unpublished rows.

## `executions` table (new)
```
job_id        UUID PRIMARY KEY REFERENCES jobs(id)   -- see ADR 003: attempt_id collapses into job_id for V0.2
worker_id     TEXT NOT NULL          -- hostname/pid identifier of the worker that ran it
started_at    TIMESTAMPTZ NOT NULL
finished_at   TIMESTAMPTZ NULL
outcome       TEXT NULL              -- 'SUCCEEDED' | 'FAILED' | 'CANCELLED', mirrors jobs.status at completion
```
`job_id PRIMARY KEY` (not a separate `id` + unique constraint) — deliberately the simplest representation of "one execution row per job" for V0.2; V0.3 changes this to `(job_id, attempt_id)` composite when retries are introduced, which is a schema migration but not a breaking one for existing rows (existing rows get `attempt_id = 1`).

## Migration sequencing
`0002_add_cancel_and_claim_columns.py`, `0003_create_outbox.py`, `0004_create_executions.py` — three separate migrations (not one), so each is independently revertible and the diff is easy to review.
