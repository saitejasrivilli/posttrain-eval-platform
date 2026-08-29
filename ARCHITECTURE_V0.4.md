# ARCHITECTURE — V0.4 (updated HLD)

Delta on top of V0.1-V0.3's architecture docs. Inserts one new process (Scheduler); does not modify the Outbox Relay, Worker's execution/heartbeat logic, or Recovery's stale-lease detection -- only extends what each of those checks.

## Component diagram

```
                 Client (curl/tests)
                        |
                        v
                 FastAPI app (unchanged)
                        |
                        v
                 Service / Repository
                        |
                        v
                  PostgreSQL
   (jobs, outbox, attempts, dlq, capacity, reservations,
    scheduling_decisions -- 3 new tables)
                    ^   ^    ^    ^
                    |   |    |    |
        +-----------+   |    |    +---------------+
        |               |    |                    |
  +-----------+   +----------+   +-----------+   +--------------+
  | Outbox    |   | Scheduler|   | Worker(N) |   | Recovery(N)  |
  | Relay     |   | (new)    |   | claim now |   | reclaim now  |
  | (V0.2,    |   | ranks +  |   | ALSO      |   | ALSO releases|
  | unchanged)|   | reserves |   | requires  |   | the reservation|
  +-----------+   +----------+   | valid     |   | tied to the  |
                                  | reservation| | LOST attempt |
                                  +-----------+   +--------------+
```

Five OS processes: API, Outbox Relay, Worker(N), Recovery(N), **Scheduler(N, new)**. Consistent with every prior version's "single deployable domain logic, multiple entrypoints" decision (V0.1 ADR 001) -- no new service boundary invented, no new language/framework, same shared repository module.

## Scheduler internals
```
poll (SCHEDULER_POLL_INTERVAL_MS)
  |
  +-- select QUEUED-and-eligible candidates (same eligibility claim() checks)
  |
  +-- rank by effective_priority (SCHEDULING_POLICY_V0.4.md)
  |
  +-- for each candidate, in rank order:
        atomic reservation transaction (ADR 007):
          re-verify eligibility -> conditional capacity UPDATE(s) -> insert
          reservation -> record decision -> commit
        (job remains QUEUED; a reservation existing is what makes it
        claimable -- see ADR 009 on why scheduling and claiming are
        deliberately separate atomic operations)
```

## Extended preconditions (the integration points with V0.2/V0.3)
- **`claim()` (V0.3, extended):** in addition to its existing WHERE conditions (`status='QUEUED'`, `cancel_requested=false`, `next_retry_at` elapsed), now ALSO requires an unreleased `reservations` row exists for `(job_id, attempt_number-that-will-result)`. A worker consuming a `job.queued` message for a job with no matching reservation simply fails to claim (same "not_claimed, safe no-op" semantics V0.2/V0.3 already established for every other claim-precondition failure) -- it does not error, it does not retry-loop, it waits for the Scheduler to eventually reserve it.
- **`reclaim_stale()` (V0.3, extended):** in the same atomic transaction that fences the old owner and marks the attempt `LOST` (ADR 004), also releases that attempt's reservation (ADR 009's "stale reservations" section) -- otherwise capacity leaks the instant a worker dies, silently regressing a guarantee V0.3 already established.
- **Terminal writes (`finalize_attempt`, V0.3, extended):** `SUCCEEDED`/`FAILED`/`CANCELLED` all release the reservation tied to the finishing attempt, in the same transaction as the status write.

## Database ownership (updated)
- `capacity` -- single row (or one row per resource-pool concept, still singular in V0.4's aggregate model), written only by the Scheduler's reservation transaction and by release operations (Worker's finalize, Recovery's reclaim).
- `reservations` -- written by Scheduler (insert), Worker/Recovery (release, i.e. set `released_at`).
- `scheduling_decisions` -- written only by Scheduler, append-only, read by a new API endpoint for debugging.

## Config additions
`SCHEDULER_POLL_INTERVAL_MS` (1000), `AGING_RATE` (points/second, see ADR 008), `PRIORITY_CEILING` (100), `MAX_ADMISSIONS_PER_PASS` (e.g. 50 -- a throughput/latency knob, not a correctness parameter, tune later).

## Explicitly NOT introduced in V0.4
Kubernetes, Ray, Slurm, Redis, per-node placement, GPU topology, multi-tenant quotas/weighted-fair-scheduling, preemption, autoscaling, reservation-expiry timers (ADR 009).
