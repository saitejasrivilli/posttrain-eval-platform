# Failure Scenarios and Acceptance Tests — V0.4

Each scenario: expected behavior, why, test plan. No claim ships without a corresponding test run (same rule as V0.1-V0.3).

## 1. Two schedulers attempt to reserve the same GPU
**Expected:** exactly one succeeds; the other's capacity UPDATE affects zero rows and it moves to its next candidate.
**Test:** two scheduler processes/threads simultaneously attempt reservation for jobs whose combined GPU request exceeds availability by exactly the contested amount; assert exactly one reservation is created, capacity never goes negative-available.

## 2. Scheduler crashes after reservation but before dispatch
**Expected:** no partial state exists -- per ADR 009, reservation and dispatch aren't sequential steps of one operation; the reservation transaction either fully committed (job claimable) or fully rolled back (nothing reserved). "Crash after reservation" simply means the crash happened after commit -- the reservation stands, correctly, same as any other committed transaction surviving its writer's death.
**Test:** commit a reservation, kill the scheduler process, assert (a) capacity remains correctly decremented, (b) the job is claimable by a worker, (c) restarting the scheduler does not create a duplicate reservation for the same attempt.

## 3. Worker dies after resources are reserved
**Expected:** this is the V0.3 lease-expiry path, extended -- Recovery's reclaim transaction now also releases the reservation tied to the `LOST` attempt, in the same transaction.
**Test:** claim (consuming the reservation via `claim()`), kill the worker, let the lease expire, run Recovery; assert the attempt is `LOST`, the job is `QUEUED` (or `FAILED`/`CANCELLED` per V0.3's branching), AND the reservation's `released_at` is now set -- capacity is available again, verified by querying `capacity`, not just by inspecting the reservation row.

## 4. Job cancellation races with scheduling
**Expected:** if cancel commits before the Scheduler's reservation transaction's re-verification step, the reservation attempt fails (re-check catches `cancel_requested=true`) and no capacity is consumed. If the reservation transaction already committed before cancel arrives, the job is `QUEUED`-with-reservation and cancellable via the normal fast path (V0.2) same as any other `QUEUED` job -- cancelling it should also release its now-orphaned reservation.
**Test:** both orderings, both deterministic; assert capacity is never left reserved for a job that ends up `CANCELLED` without ever running.

## 5. Job retry races with scheduling
**Expected:** a job whose `next_retry_at` hasn't elapsed is never in the Scheduler's candidate set (same eligibility check as `claim()`); no special handling needed beyond reusing that existing condition.
**Test:** a job mid-backoff is never assigned a reservation even if capacity is abundant; once `next_retry_at` elapses, it becomes eligible and is reserved on a later pass.

## 6. Job finishes while scheduler is processing it
**Expected:** the Scheduler's re-verification step (ADR 007) checks the job is still `QUEUED` at the moment of the reservation transaction -- if a worker somehow already claimed and finished it (only possible if it was reserved by an earlier pass and already claimed), the *current* pass wouldn't be considering it at all, since it's no longer `QUEUED`. This scenario is structurally prevented by "only QUEUED jobs are candidates," not handled by a special case.
**Test:** admit and complete a job entirely between two scheduler polls; assert the next poll's candidate list correctly excludes it (it's terminal, not `QUEUED`).

## 7. Resource release occurs twice
**Expected:** the second release attempt is a no-op (rowcount 0), not an error, not a double-decrement of `allocated_*`.
**Test:** call the release path twice for the same reservation (simulating, e.g., both a worker's finalize and a racing Recovery reclaim both attempting release); assert `capacity.allocated_gpu` reflects exactly one release's worth of decrement, not two.

## 8. Resource reservation occurs twice
**Expected:** a job cannot receive two live (unreleased) reservations for the same attempt -- the Scheduler only offers currently-unreserved `QUEUED` jobs as candidates, and the reservation table's `(job_id, attempt_number)` primary key would reject a duplicate insert attempt outright as a structural backstop.
**Test:** attempt to create two reservations for the same `(job_id, attempt_number)`; assert the second fails (PK violation or equivalent conditional check), capacity is only decremented once.

## 9. High-priority jobs continuously arrive
**Expected:** they are admitted first, correctly, per ranking (ADR 008) -- this is the *intended* behavior, not a failure mode by itself; the failure mode is scenario 10.
**Test:** a stream of P100 jobs arriving continuously; assert they are consistently admitted ahead of a static P10 job, as designed.

## 10. Low-priority jobs must not starve
**Expected:** the P10 job's `effective_priority` rises over time (ADR 008's aging formula) and it is eventually admitted within a bounded number of scheduling passes, even under continuous P100 arrival -- bounded because `PRIORITY_CEILING` caps how high it can rise, so this must be verified with a concrete, reproducible timeline, not just "eventually."
**Test:** continuous stream of P100 jobs consuming most capacity, one P10 job waiting; assert the P10 job's `effective_priority` crosses enough of the P100 jobs' priority within a measured number of passes/seconds to actually get admitted -- record the actual wait time, don't estimate it (project-wide rule).

## 11. A job requests more resources than total capacity
**Expected:** never admitted; decision reason is `exceeds_total_cluster_capacity` (SCHEDULING_POLICY_V0.4.md), distinguishing "this is currently busy" from "this can never fit" -- it does not crash-loop or retry the reservation attempt every pass (recorded once per pass as `WAITING`/`exceeds_total_cluster_capacity`, but the Scheduler doesn't treat this any differently from other `WAITING` outcomes operationally -- the distinct reason exists for observability, not different control flow).
**Test:** a job requesting `gpu=100` against a `total_gpu=4` cluster; assert it is never admitted across many passes, and its recorded reason is specifically `exceeds_total_cluster_capacity`, not a generic `insufficient_gpu_capacity`.

## 12. Resource inventory changes while jobs are queued
**Expected:** if `capacity.total_gpu` is reduced (operator action) below what's currently allocated, no *already-reserved* job is retroactively evicted (reservations aren't preemptible, RESOURCE_MODEL_V0.4.md) -- future admission attempts simply see less headroom. If increased, previously-`WAITING` jobs become admittable on the next pass without any special "capacity changed" trigger (the next poll just re-evaluates against current capacity, same as always).
**Test:** reduce/increase `capacity.total_gpu` mid-run; assert existing reservations are untouched, and admission behavior on subsequent passes reflects the new totals correctly.

## 13. Scheduler crashes and restarts
**Expected:** no recovery procedure needed -- per ADR 009, the Scheduler carries no in-memory state between passes; a restarted instance simply resumes polling and ranking from current database state.
**Test:** kill the scheduler process mid-run, restart it, assert scheduling continues correctly (previously-reserved jobs remain reserved and claimable, previously-`WAITING` jobs are re-considered) with no manual intervention.

## 14. Database becomes unavailable
**Expected:** the Scheduler's poll cycle fails closed (catch, log, retry next poll), same pattern as V0.2's Outbox Relay / V0.3's Recovery. No reservation is left in an inconsistent state, since every reservation attempt is one all-or-nothing transaction.
**Test:** stop Postgres mid-scheduling-pass; assert the Scheduler logs the error and continues polling once Postgres returns, no partial reservation exists.

## 15. Multiple schedulers make the same scheduling decision
**Expected:** covered structurally by scenario 1/ADR 009 -- both may *decide* to admit the same job in their independent ranking, but only one's atomic reservation transaction can actually succeed; this is not a special case requiring coordination between scheduler instances.
**Test:** same as scenario 1, framed at the decision-recording level -- assert exactly one `scheduling_decisions` row for that job/pass has `decision=ADMITTED`, any others (from a losing scheduler's re-check failure) are recorded `WAITING` with a coherent reason, not silently dropped or duplicated as `ADMITTED`.

## Clean-room Docker verification (required before v0.4.0 tag)
`docker compose down -v && docker compose up --build` with the Scheduler added as a new service (potentially 2 replicas to actually exercise concurrency, not just single-instance correctness). Demonstrate: N queued jobs exceeding available GPU capacity, correct partial admission (some `ADMITTED`, rest `WAITING` with reasons), a killed worker's reservation correctly released and its job's retry re-admitted, resource utilization queryable via `GET /v1/capacity` throughout.
