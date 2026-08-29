# Scheduling Policy — V0.4

Full rationale and rejected alternatives in ADR 008. This document is the operational specification: what the Scheduler actually does, in order, once per pass.

## One scheduling pass
1. **Select candidates:** all jobs with `status='QUEUED'`, `cancel_requested=false`, and (`next_retry_at IS NULL OR next_retry_at <= now()`) -- the *same* set `claim()` would itself consider eligible (ADR 007's re-verification step exists precisely so the Scheduler never admits something the V0.3 claim logic would reject).
2. **Rank candidates** by `effective_priority DESC, created_at ASC`, where:
   ```
   effective_priority = min(
       priority + AGING_RATE * queue_wait_seconds,
       PRIORITY_CEILING
   )
   queue_wait_seconds = now() - created_at   (or now() - next_retry_at-became-eligible, for a retried job -- see note below)
   ```
3. **Attempt reservation** (ADR 007's atomic transaction) for each candidate in ranked order, until either the candidate list is exhausted or a configurable per-pass admission cap is reached (`MAX_ADMISSIONS_PER_PASS`, prevents one pass from monopolizing scheduler time on a huge backlog -- tune later, not a correctness parameter).
4. **Record the decision** for every candidate considered this pass, not just the ones admitted -- a `WAITING` decision with its specific reason is exactly the debugging signal REQUIREMENTS_V0.4.md asks for.
5. Repeat on `SCHEDULER_POLL_INTERVAL_MS` (configurable, same pattern as V0.2's Outbox Relay / V0.3's Recovery polling).

## Queue-wait measurement for a retried job
A job's `created_at` reflects its *original* creation, not its most recent retry-eligibility. Using raw `created_at` for aging would make a job that failed and retried many times age faster than one that's simply been waiting since creation once -- arguably correct (it *has* been in the system longer) but worth stating as a deliberate choice, not an oversight: V0.4 uses `created_at` for aging (a job's *total* time in the system is what matters for fairness against jobs that never needed a retry), not "time since last became eligible." This is revisited if real usage shows retried jobs unfairly dominating scheduling passes.

## Decision reasons (exhaustive list for V0.4)
- `resources_available` -- admitted.
- `insufficient_cpu_capacity` / `insufficient_memory_capacity` / `insufficient_gpu_capacity` -- one reservation dimension failed; if multiple dimensions are insufficient, report the first one checked (documented order: cpu, memory, gpu) rather than inventing a "multiple reasons" structure not asked for.
- `exceeds_total_cluster_capacity` -- the job's request exceeds the cluster's *total* capacity for some dimension, not just currently-available -- this job can never be admitted until capacity itself grows; flagged distinctly from a transient `insufficient_*_capacity` so an operator doesn't wait for something that will never happen on its own.
- `admission_cap_reached` -- pass-level cap hit before this candidate was reached; it'll be reconsidered next pass (and its `effective_priority` will have grown).

## What this policy does not do
No preemption, no per-tenant fairness (ADR 008), no node-aware placement (RESOURCE_MODEL_V0.4.md). No retroactive re-ordering of admitted-and-reserved jobs -- once reserved, a job holds its capacity until it releases (terminal or LOST), regardless of what arrives afterward.
