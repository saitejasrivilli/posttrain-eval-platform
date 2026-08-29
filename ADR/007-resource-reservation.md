# ADR 007: Atomic Resource Reservation

## Status
Proposed -- pending user review before implementation.

## Context
The scheduler must decide, for a `QUEUED` job requesting `(cpu, memory_mb, gpu)`, whether the cluster currently has enough unallocated capacity, and if so, atomically claim that capacity so no other scheduler process can also claim it. The naive approach:
```
SELECT available_gpu FROM capacity;
if available_gpu >= requested_gpu:
    UPDATE capacity SET allocated_gpu = allocated_gpu + requested_gpu;
```
is a classic check-then-act race: two scheduler processes can both read "1 GPU available," both decide to admit, both UPDATE, and the cluster ends up over-committed by however many schedulers raced. This is explicitly rejected -- it is exactly the failure mode ADR 002/003/004 already taught us to avoid (dual-write / check-then-act), applied to a new resource.

## Alternatives considered

### A. Check-then-act (rejected)
Described above. Rejected outright regardless of scale -- it is not a question of probability, it's a structurally unsound pattern that this project has consistently avoided since V0.2.

### B. Application-level distributed lock (e.g. Redis lock around the check-and-update)
Rejected -- Redis is explicitly out of scope for V0.4, and more fundamentally: introducing a lock service to protect a Postgres row's consistency is solving a problem Postgres already solves natively (see option D). It would also reintroduce exactly the kind of "two systems must agree" complexity ADR 002 was written to eliminate.

### C. SELECT ... FOR UPDATE, then application-side check, then UPDATE (explicit row lock)
This works: lock the capacity row, check availability *while holding the lock*, then update it, then commit (releasing the lock). Concurrent schedulers serialize on the lock -- the second one's `SELECT ... FOR UPDATE` blocks until the first commits or rolls back, then sees the updated (already-decremented) availability. Correct, but requires two round-trips (SELECT then UPDATE) and explicit transaction-boundary discipline the developer must get right every time this pattern is used.

### D. Single conditional UPDATE, capacity check embedded in the WHERE clause (chosen)
```
capacity.allocated_gpu <- capacity.allocated_gpu + requested_gpu
   WHERE capacity.allocated_gpu + requested_gpu <= capacity.total_gpu
```
This is the exact same primitive this project has used for every atomicity requirement since V0.2 (`conditional_transition`, V0.3's `claim`/`heartbeat`/`finalize_attempt`/`reclaim_stale`): the check and the write are the *same statement*. Postgres's row-level locking on the UPDATE makes this airtight without an explicit lock statement or a second round-trip -- rowcount 1 means the reservation succeeded and capacity is now correctly decremented; rowcount 0 means it didn't fit, full stop, no partial state. Chosen because:
1. It is the one primitive this entire codebase already trusts and has proven correct four times over (outbox commit, claim, heartbeat, finalize, reclaim).
2. It requires no explicit lock management -- nothing for a future contributor to get wrong by forgetting to `FOR UPDATE` or forgetting to check within the same transaction.
3. It is strictly simpler to reason about and test than option C, for equivalent correctness.

## The reservation transaction (shape, not exact SQL -- implementation detail for the design-review stage)
Admission is one database transaction containing, in order:
1. Re-verify the job is still admittable: `QUEUED`, `cancel_requested=false`, `next_retry_at` either NULL or elapsed -- the *same* preconditions `claim()` already checks (ADR 004), because the scheduler must never admit a job the V0.3 claim logic would itself reject.
2. Attempt the conditional capacity UPDATE (option D above) for each requested resource dimension. If any one fails to match (rowcount 0), roll back the entire transaction -- no partial reservation (e.g. CPU reserved but GPU rejected) is ever left committed.
3. On success, insert a `reservations` row (job_id, attempt-scoped, resource amounts, `released_at=NULL`).
4. Record the scheduling decision (`ADMITTED`, reason `resources_available`) in the same transaction.
5. Commit. The job remains `QUEUED` at the end of this transaction -- the reservation exists, but `claim()` (V0.3, unchanged mechanism, extended precondition) is what actually transitions it to `RUNNING`. This separation matters: see ADR 009 for why scheduling and claiming are deliberately two different atomic operations rather than one, and how that stays race-free.

If any admission step fails (capacity insufficient), the transaction still commits the *scheduling decision* record (`WAITING`, reason `insufficient_<resource>_capacity`) -- deciding "not now" is itself a decision worth recording, not an error to swallow.

## Why resource reservation belongs in PostgreSQL
Same reasoning as every prior ADR in this project: Postgres is already the single source of truth for job state, leases, and attempts. Splitting capacity accounting into a separate system (even an in-memory scheduler cache) reintroduces the dual-write problem this project has spent three versions eliminating -- a scheduler process's local view of "available GPUs" would need to somehow stay consistent with the database's view of which jobs are actually running, across crashes, restarts, and multiple scheduler instances. Keeping the counter in the same transactional system as everything else it needs to be consistent with (jobs, attempts, leases) is what makes the single-atomic-UPDATE pattern above possible at all.

## Consequences
- Every resource-releasing event (SUCCEEDED/FAILED/CANCELLED/LOST) must, in the same transaction as its job-state write, also release the matching reservation -- an omission here is a slow capacity leak, not an immediate visible bug, which makes it dangerous. This is why release is treated as a first-class invariant (REQUIREMENTS_V0.4.md), not an afterthought.
- The capacity table becomes a second thing (alongside `jobs.status`) that a "did this actually happen" audit must check -- `reservations` (see DB_SCHEMA_CHANGES_V0.4.md) is the audit trail, same role `attempts` plays for execution history.
