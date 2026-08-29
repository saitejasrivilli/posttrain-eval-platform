# ADR 006: Attempt Model Migration (executions -> attempts)

## Status
Proposed -- pending user review before implementation.

## Context
ADR 003 (V0.2) deliberately collapsed the general attempt model into a degenerate single-execution-per-job shape (`executions.job_id` as primary key) and explicitly promised the migration path:
```
V0.2:  job_id -> exactly one logical execution record
V0.3+: job_id -> attempt_1, attempt_2, attempt_3 ...
```
V0.3 is that promised version. This ADR defines the migration precisely so it's additive/reversible, not a redesign.

## Decision
Replace `executions` with `attempts`:
```
attempts
  job_id            UUID NOT NULL REFERENCES jobs(id)
  attempt_number     INTEGER NOT NULL
  worker_id          TEXT NOT NULL
  status             TEXT NOT NULL   -- RUNNING | SUCCEEDED | FAILED | CANCELLED | LOST
  started_at         TIMESTAMPTZ NOT NULL
  finished_at        TIMESTAMPTZ NULL
  error_message      TEXT NULL
  error_classification TEXT NULL     -- transient | permanent | unknown
  PRIMARY KEY (job_id, attempt_number)
```
Migration: existing `executions` rows backfill into `attempts` with `attempt_number = 1`, `status` derived from their `outcome` column (unchanged mapping). `executions` table is dropped after backfill in the same migration set (one added migration to create `attempts` + backfill, one to drop `executions` -- kept as two migrations so the backfill is independently revertible from the drop).

## Why `LOST` is a fifth status, not reuse of `FAILED`
A `LOST` attempt (worker's lease expired, reclaimed by recovery) is different from a `FAILED` attempt (worker ran, execution raised an error) in a way that matters for debugging and for retry classification: `LOST` never got to report anything about the job's own correctness -- it's purely an infrastructure signal, always classified `transient` for retry purposes (ADR 005), and its `error_message` is always the synthetic `"worker_lost: lease expired at <timestamp>"`, not something the execution body produced. Collapsing it into `FAILED` would erase this distinction and make debugging "why do jobs keep dying" much harder (is it the executor or the infrastructure?).

## `attempt_number` is the fencing token, not a display-only counter
Per ADR 004, `jobs.attempt_number` (the current/latest value) is the same field used for ownership fencing. `attempts.attempt_number` for a given row is fixed at the value the attempt held when it ran -- so `attempts` is an append-only audit log of every attempt a job has ever had, while `jobs.attempt_number` is the live "which attempt is current" pointer. These are intentionally two places holding related information for two different purposes (append-only history vs. live fencing state), not duplicated state that needs to stay manually in sync -- `jobs.attempt_number` only ever moves forward via the claim/reclaim UPDATE (ADR 004), and each such UPDATE is what creates the next `attempts` row.

## Alternatives considered
- **Keep `executions` and add a separate `attempts` table alongside it:** rejected -- two tables recording overlapping information invites drift and confusion about which is authoritative. A clean replacement (with migration) is simpler to reason about and was already the promised plan.
- **Surrogate `id` primary key on `attempts` instead of composite `(job_id, attempt_number)`:** rejected -- the composite key *is* the natural identity of an attempt and directly enforces "at most one row per (job, attempt_number)" at the database level, which is exactly the invariant we want (no duplicate attempt records even under a bug, since the insert would violate the PK).

## Consequences
- Any code (or dashboard, later) that queried `executions` by `job_id` alone now queries `attempts` and must decide whether it wants "the latest attempt" (`ORDER BY attempt_number DESC LIMIT 1`) or "full history" (no limit) -- this is a deliberate behavior change from V0.2's one-row-always-exists shape, documented so nothing assumes "the" execution the way V0.2 code could.
