# REQUIREMENTS — V0.4 Resource-Aware Scheduler

## Objective
Answer: given N queued jobs and finite CPU/memory/GPU capacity, which job runs, when, and why. V0.1-V0.3 stay stable; V0.4 inserts a Scheduler between "job is QUEUED and due" (V0.3's `claim()` precondition) and "job actually starts RUNNING" -- it does not replace or duplicate the V0.3 job lifecycle.

## Functional requirements

1. **Resource requirements on a job:** `cpu` (int, cores), `memory_mb` (int), `gpu` (int, count). Simple scalar model -- no GPU model/type distinction, no per-node placement. See RESOURCE_MODEL_V0.4.md for what's deliberately excluded.
2. **Cluster resource capacity:** total CPU/memory/GPU the platform has, tracked as allocated vs. available. Single aggregate pool per resource type in V0.4 (not per-node bin-packing -- see RESOURCE_MODEL_V0.4.md).
3. **Resource reservation:** admitting a job atomically reserves its requested resources and transitions it toward `RUNNING` in one transaction -- never "check capacity" then separately "allocate." See ADR 007.
4. **Resource release:** on any terminal-for-the-attempt outcome (`SUCCEEDED`, `FAILED`, `CANCELLED`, and Recovery marking an attempt `LOST`), the reservation tied to that attempt is released exactly once. Leaked capacity is a correctness bug, not a cosmetic one.
5. **Admission control:** a job whose resource request cannot currently be satisfied is not started speculatively -- it's marked `WAITING` with a reason, left `QUEUED`, and reconsidered on a later scheduling pass.
6. **Priority:** `priority` field (0-100) on job creation, default documented (not invented per-job -- a fixed default, e.g. 50, applied when omitted).
7. **Fairness / starvation prevention:** exactly one mechanism, chosen and defended in ADR 008 (not both priority+FIFO and weighted-fair implemented "just in case").
8. **Scheduler concurrency safety:** the design must be correct with N>=1 scheduler processes running concurrently, even though V0.4 ships running one. See ADR 009.
9. **Scheduling decision reasons:** every admission decision (admitted or waiting) is recorded with a machine-readable reason (`insufficient_gpu_capacity`, `resources_available`, etc.), queryable for debugging.
10. **Queue wait tracking:** how long a job has been `QUEUED` and eligible is measurable (needed both for aging-based fairness, if chosen, and for the `queue_wait_seconds` metric).
11. **Resource utilization tracking:** allocated/available per resource type queryable at any time.

## Non-functional requirements
- No Kubernetes, Ray, Slurm, Redis, or any external scheduler in V0.4. This is a decision-making layer; V0.4 does not change how a job actually executes once admitted (still the V0.2/V0.3 worker/Kafka/lease machinery).
- PostgreSQL remains sole source of truth for capacity and reservations, same as every version before it.
- The scheduler's admission decision must not be circumventable -- a worker cannot claim a job (V0.3's `claim()`) unless a valid reservation exists for its current attempt. See how this changes `claim()`'s precondition in ARCHITECTURE_V0.4.md.

## Explicit non-goals for V0.4
- Kubernetes/Ray/Slurm integration (translation layer to a real execution backend is future scope, explicitly not built now)
- Per-node placement / bin-packing (single aggregate pool per resource type only)
- GPU topology awareness (NVLink, multi-GPU-per-job placement constraints)
- Multi-tenant quota systems, billing, cost tracking
- Autoscaling capacity up/down
- Preemption of a RUNNING job to admit a higher-priority one (V0.4 only controls admission of new claims, never interrupts something already running -- consistent with V0.2/V0.3's "cancellation is cooperative, not preemptive" precedent)

## Interaction with V0.3 (must not create a second lifecycle)
```
QUEUED (V0.3, possibly with next_retry_at gating a retry)
   |
   v
Scheduler admits (V0.4, new) -- reserves resources + creates reservation record
   |
   v
claim() (V0.3, extended) -- now ALSO requires a valid reservation to exist
   |
   v
RUNNING -- heartbeat/lease exactly as V0.3
   |
   +-- SUCCEEDED/FAILED/CANCELLED -- release reservation (new)
   +-- lease expires -> Recovery reclaims (V0.3) -- release reservation (new), mark LOST
```
V0.3's question was "can we execute reliably." V0.4's question is "which execution gets resources, and when." The state machine (STATE_TRANSITIONS_V0.2/V0.3.md) is not modified -- V0.4 inserts a gate before `QUEUED -> RUNNING`, it does not add new job statuses.

## Acceptance criteria (must all pass before v0.4.0 tag)
- [ ] No resource over-allocation under concurrent admission (proven with multiple scheduler processes/threads racing for the same scarce resource)
- [ ] No double reservation, no double release (proven under concurrency)
- [ ] A job requesting more than total cluster capacity is never admitted, waits forever with a clear reason (not silently dropped, not crash-looped)
- [ ] Cancelled jobs are never admitted; retry-not-yet-due jobs are never admitted (scheduler respects the same preconditions `claim()` already enforces, does not bypass them)
- [ ] A worker cannot execute without a valid reservation for its current attempt
- [ ] Reservation is released exactly once for every terminal/LOST outcome, verified by test, not by inspection alone
- [ ] Starvation prevention mechanism demonstrably prevents indefinite starvation of a low-priority job under continuous higher-priority arrivals, with a documented bound
- [ ] Scheduler crash after reservation but before job claim does not leak the reservation or leave the job unschedulable forever
- [ ] Live clean-room demonstration: N queued jobs, finite GPU capacity, multiple scheduler processes, correct allocation with no overcommit, resources released and reusable after completion
- [ ] All numbers/claims trace to actual test/run output (same rule as V0.1-V0.3)
