# ADR 009: Scheduler Concurrency Safety

## Status
Proposed -- pending user review before implementation.

## Context
V0.4 ships running one Scheduler process, but the design must already be correct with N>=1 -- adding a second instance later must never require a redesign, only starting another process (same posture V0.2 took with Worker replicas, V0.3 with Recovery replicas). The Scheduler's job is: rank admittable `QUEUED` jobs (ADR 008), attempt reservation for each in rank order (ADR 007), stop when capacity runs out for this pass.

## Why scheduling and claiming are two separate atomic operations, not one
An earlier framing might try to make "reserve resources" and "claim the job" (V0.3's `QUEUED -> RUNNING` transition) a single combined operation. Rejected: the Scheduler and the Worker are different processes with different responsibilities and different failure domains (a Scheduler doesn't execute anything; a Worker doesn't rank jobs). Combining them would mean every Worker replica also needs to embed scheduling-policy logic, or every scheduling decision would need to block on a specific worker being ready to immediately claim -- neither is desirable. Keeping them separate means:
- The Scheduler's transaction (ADR 007) only ever touches `capacity`, `reservations`, and the scheduling-decision log -- it does not touch `jobs.status` at all. A job with a reservation is still `QUEUED`.
- `claim()` (V0.3, extended) is what a Worker calls when it actually picks up a message -- its precondition now additionally requires an unreleased reservation exists for this job's current attempt.
- This means a race between "Scheduler reserves job X" and "does a Worker exist to claim it" is not a correctness problem -- if no worker claims it for a while, the reservation just sits there, correctly holding the capacity, until a worker does. (Whether a reservation should ever *expire* if unclaimed for too long is addressed below, under "stale reservations.")

## Two schedulers racing for the same capacity
Covered structurally by ADR 007's single-conditional-UPDATE mechanism: both scheduler processes' reservation attempts for the same GPU are literally the same SQL statement pattern racing on the same row; Postgres row-level locking serializes them, exactly as it has for every other concurrent-writer scenario in this project since V0.2. No new mechanism -- this ADR exists to state explicitly that scheduler-level concurrency is *not* a new problem class requiring new machinery, it is the same problem V0.2/V0.3 already solved, applied to a new table.

## Two schedulers making the same scheduling decision
If both schedulers independently rank the same job as "next to consider," only one of their reservation attempts can succeed (the capacity UPDATE ensures this) -- the other's attempt fails to match (rowcount 0 on *its* capacity UPDATE, because the first one already consumed the capacity), and it moves on to the next candidate in its own ranking. No coordination between scheduler instances is required; each one's transaction is independently atomic against the shared capacity row.

## Scheduler crash after reservation but before dispatch
"Dispatch" in this design is not a separate step the Scheduler performs -- admission (ADR 007) already commits an outbox-style visibility: once a reservation exists and the job is `QUEUED`, any Worker consuming from Kafka (V0.2 machinery, unchanged) can claim it whenever the message arrives or is redelivered. A Scheduler crash immediately after committing a reservation is *not* a partial state -- the transaction either committed (reservation exists, capacity correctly decremented, job stays `QUEUED` and claimable) or it didn't (rolled back, capacity untouched). There is no intermediate "reserved but not yet dispatched" state requiring recovery, because reservation and dispatch are not sequential steps in one operation -- the reservation's mere existence *is* what makes the job claimable.

## Stale reservations (a new failure mode V0.4 introduces)
A reservation can outlive its usefulness in two ways:
1. **Job never gets claimed** (no worker picks up the message for a long time despite a valid reservation) -- capacity sits reserved but idle. V0.4 does not solve this with a timeout/expiry mechanism; a reservation is released only by the job reaching a terminal-for-the-attempt state (ADR 007's release invariant) or by Recovery reclaiming it (below). An unclaimed-but-reserved job is a worker-capacity problem (not enough workers running), not a scheduler bug -- documented as a known limitation, not silently mitigated by inventing a reservation-expiry timer with no clear correct value.
2. **The job's attempt is later marked `LOST`** (V0.3's Recovery process, worker died). The reservation tied to that attempt must be released in the *same* transaction Recovery already uses to mark the attempt `LOST` and move the job to `QUEUED`/`FAILED`/`CANCELLED` (ADR 004's reclaim UPDATE) -- otherwise the capacity leaks silently the moment a worker dies, which would be a severe regression hiding inside a feature (V0.3's recovery) this project already shipped and trusted. This is the single most important integration point between V0.3 and V0.4, and it must be tested explicitly (see FAILURE_SCENARIOS_V0.4.md).

## Multiple scheduler passes and job re-ranking
Each scheduling pass is independent and stateless between passes (no scheduler-local memory of "what I decided last time" -- the database is the only state). A job not admitted in pass N (insufficient capacity) is simply re-ranked and reconsidered in pass N+1, N+2, etc., with its `effective_priority` (ADR 008) naturally increasing each time due to accumulated wait -- this is what actually prevents starvation across many passes, not a special "remember this job was skipped" mechanism.

## Consequences
- No distributed lock, no leader election, no single "active scheduler" designation is needed -- any number of scheduler processes can run identically and safely, exactly like V0.2's Worker/V0.3's Recovery replicas.
- The absence of a reservation-expiry timer is a deliberate, documented limitation (unclaimed-but-reserved jobs are a capacity-planning/worker-count problem), not an oversight -- revisit only if operational experience shows it matters.
