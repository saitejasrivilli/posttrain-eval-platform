# ADR 008: Scheduling Policy -- Priority with Bounded Aging

## Status
Proposed -- pending user review before implementation.

## Context
Given more admittable jobs than capacity at any scheduling pass, the scheduler needs a deterministic order to consider them in. Pure FIFO ignores urgency (a P100 production eval sits behind 500 P10 experiments). Pure priority (always run the highest-priority QUEUED job first) starves low-priority work indefinitely if higher-priority jobs keep arriving -- exactly the failure mode REQUIREMENTS_V0.4.md calls out.

## Alternatives considered

### A. Priority + FIFO within priority band (rejected as the sole mechanism)
Simple: order by `(priority DESC, created_at ASC)`. Easy to implement, easy to reason about -- but provides zero starvation protection. A P10 job queued at 9:00am can wait forever if P50/P100 jobs keep arriving after it. This directly violates the "low-priority jobs must not starve" requirement, so it cannot be the *only* mechanism, though its ordering-within-band logic is still used (see chosen design).

### B. Weighted fair scheduling (rejected for V0.4, not forever)
Tracks per-tenant/team resource consumption over a window and allocates proportionally to configured weights (e.g. Team A gets 70% of capacity, Team B 30%, regardless of raw job counts). This is the *correct* answer once the platform has real multi-tenancy with meaningfully different teams competing for a shared cluster. Rejected for V0.4 specifically because:
- It requires a notion of "tenant" or "team" that doesn't exist anywhere in the schema yet (V0.4 has no auth, no multi-tenancy -- explicitly out of scope per every prior version's scorecard).
- It requires tracking consumption *history* over a rolling window, which is meaningfully more state and more testing surface (what window? how is historical usage decayed? what happens to a new tenant with no history?) than a single scalar per job.
- Building it now, without a real tenant concept to hang it on, would be speculative infrastructure -- exactly the kind of premature complexity this project has consistently avoided (see V0.1 ADR 001's "no speculative schema fields," V0.2 ADR 003's degenerate-attempt-model discipline). WFQ is the correct V0.5+/V0.8+ answer once multi-tenancy exists; building its accounting machinery today with nothing real to account for is guessing.

### C. Priority + bounded aging (chosen)
```
effective_priority = min(
    priority + aging_rate * queue_wait_seconds,
    priority_ceiling
)
```
Scheduler considers jobs in `effective_priority DESC, created_at ASC` order (the FIFO tiebreak from option A still applies within an effective-priority band). A job's effective priority rises the longer it waits, eventually reaching (but never exceeding) `priority_ceiling` -- capped so that an old low-priority job asymptotically approaches "as urgent as anything gets" but can never mathematically outrank a job that arrives with genuinely maximum priority and immediate need. This is the "unbounded aging" danger the requirements called out, addressed by the cap.

**Why chosen over A alone:** solves the exact starvation case A cannot.
**Why chosen over B for now:** requires no new schema concept (uses `priority`, `created_at` -- both already needed anyway), no historical usage tracking, no tenant model. It is the simplest mechanism that actually satisfies the stated requirement ("low-priority jobs must not starve"), not the simplest mechanism period -- option A is simpler but insufficient, which is why it's rejected rather than chosen for being easier.

## Parameters (configurable, not hardcoded)
`AGING_RATE` (effective-priority points gained per second waited) and `PRIORITY_CEILING` (default: max priority value, 100 -- an aged job can reach parity with a genuinely max-priority job but never exceed it, preserving "real urgency" as the one thing that can never be out-ranked by mere waiting). Both configurable per REQUIREMENTS_V0.4.md's "no invented numbers without a way to tune them" precedent (same posture as V0.3's `LEASE_DURATION_SECONDS` etc.).

**The aging-rate tradeoff, stated explicitly:** too high, and effective priority saturates quickly -- aging stops meaningfully distinguishing "waited 2 minutes" from "waited 20 minutes," and a merely-patient job starts crowding out genuinely time-sensitive new arrivals sooner than intended. Too low, and aging fails to rescue a starving job within any reasonable operational timeframe -- the mechanism exists on paper but never actually fires in practice. No specific numeric default is claimed as "correct" here; V0.4 picks a conservative starting value and this is explicitly a candidate for real tuning once V1.0's load testing produces actual queue-wait distributions to tune against.

## Consequences
- `effective_priority` is computed at scheduling-decision time (not stored/maintained continuously) -- it's a read-time computation over `priority` and `now() - created_at`, so it requires no background job to keep it updated and cannot drift out of sync with reality.
- Every scheduling pass re-ranks the full admittable set; this is O(n log n) per pass over the currently-`QUEUED`-and-eligible set, not over all jobs ever created (terminal jobs aren't part of this set) -- acceptable at V0.4's scale, revisit only if a real load test shows it matters.
- This ADR does not solve multi-tenant fairness. If/when real multi-tenancy arrives, weighted fair scheduling (option B) becomes the right upgrade, layered on top of (not replacing) priority+aging within a tenant's own share.
