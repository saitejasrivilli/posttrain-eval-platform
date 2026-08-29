# Database Schema Changes — V0.4

Additive, migration-based, same discipline as V0.1-V0.3. No V0.1-V0.3 columns/tables removed.

## `jobs` table (extend)
New column:
- `priority` INTEGER NOT NULL DEFAULT 50 -- see RESOURCE_MODEL_V0.4.md, plain scalar, no named tiers.

Resource request itself is NOT a new set of columns -- it's read from the existing `jobs.config` JSON field (`config.resources.cpu/memory_mb/gpu`), consistent with how V0.2's simulated executor already reads `config.sleep_seconds`. Adding dedicated columns for something still this simple and still executor-simulated would be premature schema commitment (same reasoning as V0.1 ADR 001's "no speculative fields").

## `capacity` table (new)
```
id                    fixed singleton row (e.g. a single well-known UUID, or SERIAL PK with exactly one row enforced by application discipline)
total_cpu             INTEGER NOT NULL
allocated_cpu         INTEGER NOT NULL DEFAULT 0
total_memory_mb       INTEGER NOT NULL
allocated_memory_mb   INTEGER NOT NULL DEFAULT 0
total_gpu             INTEGER NOT NULL
allocated_gpu         INTEGER NOT NULL DEFAULT 0
updated_at            TIMESTAMPTZ NOT NULL
```
Single aggregate pool (RESOURCE_MODEL_V0.4.md) -- one row for V0.4. Seeded via a migration or an operator-run command with real configured cluster capacity (not invented -- documented as an operational setup step, same posture as any other environment-specific config).

## `reservations` table (new)
```
job_id          UUID NOT NULL REFERENCES jobs(id)
attempt_number  INTEGER NOT NULL
cpu             INTEGER NOT NULL
memory_mb       INTEGER NOT NULL
gpu             INTEGER NOT NULL
created_at      TIMESTAMPTZ NOT NULL
released_at     TIMESTAMPTZ NULL
PRIMARY KEY (job_id, attempt_number)
```
Composite PK mirrors `attempts` (ADR 006's precedent) -- one reservation per attempt, never reused across retries. Partial index `(released_at) WHERE released_at IS NULL` for the "does this job have a live reservation" check `claim()` performs.

## `scheduling_decisions` table (new)
```
id                UUID PRIMARY KEY
job_id            UUID NOT NULL REFERENCES jobs(id)
decided_at        TIMESTAMPTZ NOT NULL
decision          TEXT NOT NULL     -- ADMITTED | WAITING
reason            TEXT NOT NULL     -- see SCHEDULING_POLICY_V0.4.md's exhaustive list
requested_cpu     INTEGER NOT NULL
requested_memory_mb INTEGER NOT NULL
requested_gpu     INTEGER NOT NULL
available_cpu_snapshot INTEGER NOT NULL   -- capacity.total_cpu - allocated_cpu at decision time
available_memory_mb_snapshot INTEGER NOT NULL
available_gpu_snapshot INTEGER NOT NULL
effective_priority NUMERIC NOT NULL
```
Append-only audit log, one row per (job, scheduling pass considered) -- answers "why was this job admitted or not" without needing to reconstruct historical capacity state from logs. Snapshot columns capture what the Scheduler *saw*, not what capacity is *now* (those can differ by the time someone queries this later).

## Migration sequencing
1. `0009_add_priority_to_jobs.py`
2. `0010_create_capacity.py` (creates table + seeds the single row -- seed values are an explicit migration-time input, not hardcoded silently)
3. `0011_create_reservations.py`
4. `0012_create_scheduling_decisions.py`

Four separate migrations, each independently revertible, same discipline as every prior version.
