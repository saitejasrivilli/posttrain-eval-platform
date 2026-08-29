# Database Schema Changes — V0.3

Additive, migration-based, same discipline as V0.1/V0.2. `executions` (V0.2) is replaced per ADR 006's promised path, with an explicit backfill migration, not silently dropped.

## `jobs` table (extend)
New columns:
- `attempt_number` INTEGER NOT NULL DEFAULT 0 -- the fencing token (ADR 004) and current attempt count. Incremented only by the claim/reclaim UPDATE.
- `lease_owner` TEXT NULL -- worker_id currently holding the lease, NULL when not `RUNNING`.
- `lease_expires_at` TIMESTAMPTZ NULL -- NULL when not `RUNNING`.
- `next_retry_at` TIMESTAMPTZ NULL -- set when a transient failure returns the job to `QUEUED` for retry; NULL otherwise (including on initial creation -- V0.2's auto-queue-on-creation path leaves this NULL, meaning "eligible now," not "wait until governed by retry logic").

No changes to existing V0.1/V0.2 columns.

## `attempts` table (new, replaces `executions`)
```
job_id                UUID NOT NULL REFERENCES jobs(id)
attempt_number        INTEGER NOT NULL
worker_id             TEXT NOT NULL
status                TEXT NOT NULL       -- RUNNING | SUCCEEDED | FAILED | CANCELLED | LOST
started_at            TIMESTAMPTZ NOT NULL
finished_at           TIMESTAMPTZ NULL
error_message         TEXT NULL
error_classification  TEXT NULL           -- transient | permanent | unknown
PRIMARY KEY (job_id, attempt_number)
```
See ADR 006 for the full rationale, including why `LOST` is a distinct status from `FAILED`.

## `dlq` table (new)
```
job_id                UUID PRIMARY KEY REFERENCES jobs(id)
moved_to_dlq_at        TIMESTAMPTZ NOT NULL
last_attempt_number    INTEGER NOT NULL
last_error_message     TEXT NULL
last_error_classification TEXT NOT NULL
total_attempts         INTEGER NOT NULL
```
`job_id PRIMARY KEY` -- a job enters the DLQ at most once per its terminal `FAILED` transition (V0.3 has no DLQ redrive that would create a new job attempt after a DLQ entry exists; that's future scope). Answers, per REQUIREMENTS_V0.3.md: which job (`job_id`), which attempt (`last_attempt_number`), why (`last_error_message`/`last_error_classification`), which worker (join `attempts` on `job_id, last_attempt_number` for `worker_id`), when (`moved_to_dlq_at`), how many attempts (`total_attempts`).

## Migration sequencing
1. `0005_add_lease_and_retry_columns.py` -- adds the four new `jobs` columns.
2. `0006_create_attempts_and_backfill.py` -- creates `attempts`, backfills from `executions` (`attempt_number=1`, `status` derived from `executions.outcome`, `worker_id`/`started_at`/`finished_at` copied directly).
3. `0007_drop_executions.py` -- drops `executions` now that all data lives in `attempts`. Kept as its own migration so the backfill (step 2) is independently revertible without also losing the drop's reversibility.
4. `0008_create_dlq.py` -- creates `dlq`.

Four separate migrations, each independently revertible, same discipline as V0.1's three-migration sequencing.
