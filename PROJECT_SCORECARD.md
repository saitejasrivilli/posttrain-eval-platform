# PROJECT SCORECARD

Tracks version gate status. A version is NOT tagged/pushed until every acceptance criterion for it is checked and verified (not self-reported).

## V0.1 — Foundation
Status: **COMPLETE — v0.1.0**

| Capability | Status | Evidence |
|---|---|---|
| Job CRUD (create/get/list/patch/delete) | Done | `app/routers/jobs.py`, `app/services/jobs.py`, `app/repository/jobs.py`; `tests/test_jobs_integration.py::test_full_job_lifecycle`; live curl cycle verified against `docker compose up --build` |
| PostgreSQL persistence | Done | `app/models/job.py`, `app/db.py`; all integration tests run against real Postgres, not mocked |
| Alembic migrations | Done | `alembic/versions/0001_create_jobs.py`; verified reproducible from clean volume via `docker compose down -v && up --build`, and via manual `DROP TABLE` + `alembic upgrade head` |
| Router→service→repository layering | Done | jobs and health both follow this; `app/repository/health.py` + `app/services/health.py` added to remove SQL-in-router violation found in release review; `tests/test_health.py::test_health_router_does_not_access_db_directly` |
| Health/readiness endpoints | Done | `app/routers/health.py`; `tests/test_health.py`; live-verified: `/healthz`=200, `/readyz`=200 (db up), 503 (db stopped), 200 (db restarted) — both in dev run and clean-room rebuild |
| Pagination | Done | `app/services/jobs.py` (limit 1-200, offset >=0 validation); `tests/test_jobs_unit.py` (empty page, last page, out-of-range limit/offset); `tests/test_jobs_integration.py::test_pagination` |
| Structured logging | Done | `app/logging_conf.py` (JSON request logs: request_id, method, path, status, latency_ms) |
| Config via env vars, no hardcoded secrets | Done | `app/config.py`, `.env.example`, `.gitignore` excludes `.env` |
| Docker Compose local dev | Done | `docker-compose.yml`, `Dockerfile`; verified `docker compose up --build` starts API+DB with zero manual steps, clean-room tested with `-v` volume wipe |
| CI (GitHub Actions) | Done | `.github/workflows/ci.yml` — Postgres service container, migration + pytest on every PR |
| Unit tests | Done | `tests/test_jobs_unit.py` (8 tests: job creation defaults to PENDING, 404 on missing, pagination bounds) |
| Integration tests | Done | `tests/test_jobs_integration.py` (3 tests: full lifecycle, 404 after delete, pagination) |
| Status field exists, transitions NOT enforced | Done (documented gap) | `ARCHITECTURE.md` lifecycle section states enforcement is deferred to V0.2; live-verified PATCH accepts PENDING→SUCCEEDED skip and arbitrary status strings without rejection, matching the documented behavior |

**Release note (v0.1.0):** 15/15 tests passed. Clean-room Docker verification passed (`docker compose down -v` + `up --build` from a fresh volume, migrations applied automatically, full CRUD cycle re-verified via curl). PostgreSQL failure/recovery was live-tested (`/readyz` 200 → 503 on `docker compose stop db` → 200 on restart), both pre- and post-layering-fix. No release blockers remain.

## Explicitly deferred (not implemented, not claimed)
- Kafka
- Asynchronous workers
- Distributed scheduler
- Idempotency
- Retry engine
- Dead-letter queue (DLQ)
- Enforced job state machine
- Authentication / authorization
- GPU scheduling
- Kubernetes
- Ray
- Distributed execution

## V0.2 — Durable Asynchronous Job Execution
Status: **COMPLETE — v0.2.0**

| Capability | Status | Evidence |
|---|---|---|
| Enforced job state machine | Done | `app/statemachine.py`, `app/services/jobs.py::transition`; `tests/test_state_machine.py` (illegal transition 409, terminal-state immutability); live: `CANCELLED -> RUNNING` rejected 409 |
| Atomic job claiming | Done | `app/repository/jobs.py::conditional_transition` (single UPDATE...WHERE status=); `tests/test_worker.py::test_concurrent_workers_only_one_claims_the_job` (5 threads, real Postgres, 1 claimed/4 no-op) |
| Transactional outbox | Done | `app/repository/jobs.py::create_and_enqueue`, `app/models/outbox.py`; `tests/test_outbox.py::test_create_job_writes_job_and_outbox_atomically` |
| At-least-once transport (worker-ack + relay-publish crash windows) | Done — **proven live, not just unit-tested** | Manual crash-window verification against real Redpanda + real Postgres (this session): worker process killed before Kafka offset commit -> redelivery at same offset -> idempotency no-op, exactly 1 execution row; outbox relay process killed after real Kafka publish ack but before `published_at` marked -> relay restart republished (2 Kafka offsets, same job) -> worker: first `claimed`, duplicate `not_claimed`, exactly 1 execution row both times. See conversation transcript for full job_id/offset/log evidence. |
| Idempotent execution | Done | `app/services/worker.py::process_job_message`, `executions` unique-per-job constraint; `tests/test_worker.py::test_duplicate_delivery_after_completion_is_a_no_op` + live proof above |
| Job cancellation (cooperative) | Done | `POST /v1/jobs/{id}/cancel`; `tests/test_cancellation.py` (immediate for QUEUED, flag-based for RUNNING, 409 on terminal); live-verified |
| Worker crash after claim (orphaned RUNNING) | Documented limitation, not solved | `FAILURE_SCENARIOS_V0.2.md` invariant + `tests/test_worker.py::test_worker_crash_after_claim_leaves_job_running` proves no accidental auto-recovery exists. Explicitly deferred to V0.3 (heartbeat/lease/stale-job detection). |
| Kafka-down / Postgres-down failure modes | Done | Live-verified: job creation succeeds with Kafka down (outbox buffers, publishes on recovery); `/readyz` 503 + job creation 500 with Postgres down, clean recovery after restart |
| Clean-room Docker verification | Done | `docker compose down -v && up --build` (API + worker + outbox-relay + Redpanda + Postgres), full async lifecycle `QUEUED -> RUNNING -> SUCCEEDED` observed via curl + logs |
| Unit/integration tests | Done | 31/31 passing (`tests/test_state_machine.py`, `test_outbox.py`, `test_worker.py`, `test_cancellation.py` + updated V0.1 suites), all against real Postgres |

**Release note (v0.2.0):** Validated at-least-once Kafka delivery across worker-ack and outbox-relay crash windows, with idempotent consumption preventing duplicate logical execution. Transport is at-least-once, not exactly-once — effectively-once logical execution is achieved through conditional-UPDATE claiming + execution-record idempotency, not through any Kafka delivery guarantee. Orphaned `RUNNING` jobs (worker dies mid-execution) remain unrecovered by design — V0.3 scope. No Redis/Kubernetes/Ray/priority-scheduling/heartbeats/DLQ/auth introduced.

## Explicitly deferred (not implemented, not claimed) — updated for V0.2
- Kafka consumer/worker heartbeat, lease, stale-job detection (V0.3)
- Retry engine, dead-letter queue (V0.3)
- Priority/fairness scheduling, resource-aware scheduling (V0.4)
- Authentication / authorization
- GPU scheduling
- Kubernetes
- Ray
- Multi-attempt idempotency keys (V0.2 collapses `attempt_id` into `job_id`; see ADR 003)

## V0.3 — Failure Recovery & Retry Engine
Status: **COMPLETE — v0.3.0**

| Capability | Status | Evidence |
|---|---|---|
| Worker leases | Done | `jobs.lease_owner`/`lease_expires_at` (migration 0005); `app/repository/jobs.py::claim` grants a lease atomically on claim |
| Heartbeat | Done | `app/services/worker.py::_HeartbeatLoop` — independent background thread/timer, not blocked by execution body; `tests/test_worker_heartbeat_thread.py::test_real_heartbeat_thread_keeps_slow_job_alive` (real thread, 3s simulated body vs 1s lease, never reclaimed) |
| Fencing (all four stale-write paths) | Done | `app/repository/jobs.py::heartbeat/finalize_attempt` — every write requires `status='RUNNING' AND lease_owner=:worker_id AND attempt_number=:n` in one atomic UPDATE. Proven for all four paths: `tests/test_leases_and_recovery.py::test_split_brain_original_worker_heartbeat_also_rejected` (heartbeat), `test_split_brain_original_worker_cannot_commit_after_reclamation` (success), `tests/test_release_readiness_v0_3.py::test_split_brain_stale_failed_write_rejected` (failure), `test_split_brain_stale_retry_requeue_write_rejected` (retry/requeue) |
| Stale-job recovery | Done | `recovery/main.py`, `app/services/recovery.py::reclaim_stale_leases`; `tests/test_leases_and_recovery.py::test_stale_lease_is_reclaimed_and_old_attempt_marked_lost`; **live**: real `docker kill` on worker mid-execution, real lease expiry, real Recovery process reclaimed the job, dispatched retry, new worker container completed it to `SUCCEEDED` — full transcript in conversation history |
| Split-brain protection | Done | `app/repository/jobs.py::reclaim_stale` (moves `status` off `RUNNING`, which alone invalidates every future write from the old owner — no token bump needed for fencing correctness); `tests/test_leases_and_recovery.py::test_split_brain_original_worker_cannot_commit_after_reclamation` (release-blocking test, passing) |
| Attempt tracking | Done | `attempts` table replaces `executions` (ADR 006, migrations 0006/0007, backfilled not dropped-and-lost); `attempt_number` advances only on `claim()`, never on reclaim — corrected during implementation after catching a numbering-gap bug, documented inline in ADR 004 |
| `LOST` vs `FAILED` distinct | Done | `app/repository/attempts.py::mark_lost` (Recovery-only path, `error_classification=transient`) vs worker's own `FAILED` path (execution-body error) — structurally separate code paths, never conflated |
| Retry classification (transient/permanent/unknown) | Done | `app/retry_policy.py`; `tests/test_worker.py::test_worker_retries_transient_failure_then_succeeds`, `test_permanent_failure_goes_straight_to_dlq`, `tests/test_release_readiness_v0_3.py::test_unknown_classification_is_bounded_not_infinite_retry` (unclassified failure still bounded by MAX_ATTEMPTS, not infinite) |
| Exponential backoff + jitter | Done | `app/retry_policy.py::compute_next_retry_at` (`base * 2^(n-1)`, capped, `+jitter_ratio` random); enforced atomically via `claim()`'s `next_retry_at <= now()` WHERE-clause condition, not merely recorded; `tests/test_leases_and_recovery.py::test_retry_backoff_*` (3 tests: monotonic growth, cap, jitter randomness) |
| Max-attempt handling | Done | `tests/test_worker.py::test_max_attempts_exhausted_goes_to_dlq` |
| Dead-letter queue | Done | `dlq` table (migration 0008), `GET /v1/dlq`; populated on permanent-classified failure or attempts-exhausted; tested in the above two tests |
| Cancellation races | Done | Claim-vs-cancel (`tests/test_cancellation.py::test_cancel_requested_before_retry_claim_is_honored`); **Recovery-vs-cancel** (`tests/test_release_readiness_v0_3.py::test_recovery_does_not_resurrect_a_cancelled_job` — orphaned + cancelled job lands on `CANCELLED`, never `QUEUED`, closing the specific race flagged in review) |
| Recovery-process failure handling | Done | `recovery/main.py` fails closed (catch/log/continue, same pattern as V0.2's outbox relay); `tests/test_release_readiness_v0_3.py::test_recovery_crash_mid_cycle_leaves_other_stale_job_untouched_and_reclaimable` |
| Concurrent recovery (no double-reclaim) | Done | `tests/test_leases_and_recovery.py::test_two_recovery_processes_race_the_same_stale_job` (5 threads, exactly 1 wins) |
| Clean migration | Done | 8/8 migrations (`0001`-`0008`) applied from scratch, this session, both locally and in the Docker image; `executions -> attempts` backfilled (ADR 006), not silently dropped |
| Live end-to-end worker failure/recovery | Done | Real `docker compose` stack (api/worker/outbox-relay/recovery/Redpanda/Postgres), real `docker kill` on the worker mid-execution, full `RUNNING -> LOST -> QUEUED -> RUNNING(attempt 2) -> SUCCEEDED` cycle observed via API + logs |
| Unit/integration tests | Done | 51/51 passing, all against real Postgres (no mocks in fencing/lease/retry logic) |

**Release note (v0.3.0):** Fencing token (`attempt_number`, combined with `lease_owner` and `status='RUNNING'`) makes every worker write — heartbeat, success, failure, and retry/requeue alike — conditional on continued ownership, verified for all four paths individually. Split-brain (an original worker resuming after reclamation and attempting to commit a result) is structurally prevented, not merely discouraged: the database rejects the write (0 rows affected) regardless of the old worker's belief about its own state. `attempt_number` advances only at `claim()`; reclaim fences the old owner purely by moving `status` off `RUNNING` — this was a design correction made during implementation after an earlier draft's reclaim-side increment created an attempt-numbering gap, documented in ADR 004 rather than silently fixed. Retry policy uses jittered exponential backoff, enforced atomically as a claim precondition (not merely recorded). Cancellation is deterministic against both a competing claim and a competing Recovery reclaim. No exactly-once claims made anywhere in V0.3 documentation.

## Explicitly deferred (not implemented, not claimed) — updated for V0.3
- Kubernetes
- Redis
- Ray
- Priority/fairness/resource-aware scheduling (V0.4)
- Autoscaling
- DLQ redrive/reprocessing tooling
- Sophisticated jitter (decorrelated/equal-jitter algorithms) — simple additive-random jitter only, no load-test evidence yet that more is needed
- Authentication / authorization
- GPU scheduling
- Multi-region infrastructure
- Workflow DAGs

## V0.4 — Resource-Aware Scheduler
Status: **COMPLETE — v0.4.0**

| Capability | Status | Evidence |
|---|---|---|
| Resource model (CPU/memory/GPU request + aggregate cluster capacity) | Done | `app/models/capacity.py`, `RESOURCE_MODEL_V0.4.md` (deliberate aggregate-pool-only simplification, no per-node placement) |
| Atomic resource reservation | Done | `app/repository/capacity.py::try_reserve` — single conditional UPDATE, check-and-act in one statement (ADR 007), same primitive as V0.2/V0.3's fencing UPDATEs; `tests/test_scheduler.py::test_admits_when_capacity_available` |
| No over-allocation under concurrent schedulers | Done | `tests/test_scheduler.py::test_no_overcommit_under_concurrent_schedulers` (5 threads, 10 GPUs requested vs 8 available, allocated never exceeds total) |
| Resource conservation invariant (`allocated == sum(active reservations)`) | Done | Same test + `test_release_idempotency_and_conservation_invariant` |
| Reservation identity `(job_id, attempt_number)`, never reused across retries | Done | `app/models/reservation.py`; `tests/test_scheduler_integration.py::test_worker_retry_releases_old_reservation_new_attempt_needs_new_one` |
| Idempotent release (ACTIVE→RELEASED conditional transition) | Done | `app/repository/reservations.py::release`; `tests/test_scheduler.py::test_release_idempotency_and_conservation_invariant` (double-release proven not to double-decrement) |
| Release on every terminal path: SUCCEEDED/FAILED/CANCELLED/LOST | Done | `tests/test_scheduler_integration.py::test_worker_success_releases_reservation`, `test_worker_retry_releases_old_reservation...` (FAILED), `test_cancel_releases_reservation_for_unclaimed_job` (CANCELLED), `test_recovery_releases_reservation_atomically_with_marking_lost` (LOST) |
| Priority + bounded aging (starvation prevention) | Done | `app/services/scheduler.py::_effective_priority` (ADR 008, chosen over weighted-fair-scheduling — no tenant model exists yet); `tests/test_scheduler.py::test_effective_priority_aging_lets_low_priority_eventually_rank_above_high_priority`, `test_effective_priority_never_exceeds_ceiling`; live: 10-job/4-GPU demo admitted strictly by priority |
| Admission control: cancelled / retry-not-due jobs never admitted | Done | `app/repository/jobs.py::list_schedulable`; `tests/test_scheduler_integration.py::test_scheduler_never_admits_cancelled_job`, `test_scheduler_never_admits_retry_not_yet_due` |
| `insufficient_*` vs `exceeds_total_cluster_capacity` distinction | Done | `app/repository/capacity.py::which_dimension_insufficient`; `tests/test_scheduler.py::test_exceeds_total_capacity_reason_is_distinct`, `test_insufficient_vs_exceeds_capacity_distinction` |
| Worker claim requires a valid reservation (hard invariant, scheduler cannot be bypassed) | Done | `app/repository/jobs.py::claim` (`EXISTS` subquery scoped to `attempt_number = Job.attempt_number+1 AND status='ACTIVE'`); `tests/test_scheduler_integration.py::test_claim_requires_a_valid_reservation_hard_invariant` |
| Scheduler concurrency (multiple schedulers, no coordination needed) | Done | Same conditional-UPDATE mechanism proven for capacity in the no-overcommit test (ADR 009 — no distributed lock, no leader election) |
| Scheduler crash durability (reservation + outbox survive, no stranding) | Done | `tests/test_scheduler_dispatch_boundary.py::test_reservation_and_outbox_survive_a_simulated_scheduler_crash` |
| Dispatch boundary: durable outbox insert, never a synchronous Kafka call inside the reservation transaction | Done | `tests/test_scheduler_dispatch_boundary.py::test_admission_never_calls_kafka_directly` (structural) + same crash-survival test (behavioral) |
| Recovery↔reservation integration (release atomic with marking LOST) | Done | `app/services/recovery.py::reclaim_stale_leases` (single transaction: reclaim + mark LOST + release reservation); `tests/test_scheduler_integration.py::test_recovery_releases_reservation_atomically_with_marking_lost` |
| Clean migration | Done | 12/12 migrations (`0001`-`0012`) applied from scratch, this session, locally and in the Docker image |
| Live multi-container verification | Done | Real `docker compose` stack (api/worker/outbox-relay/recovery/scheduler/Redpanda/Postgres): 10 jobs × 1 GPU request vs 4-GPU cluster capacity — all admitted strictly by priority, executed, capacity settled back to 0 with zero leaks |
| Unit/integration tests | Done | 70/70 passing, all against real Postgres |

**Release note (v0.4.0):** Resource accounting is enforced by the same single-conditional-UPDATE primitive this project has trusted since V0.2 (check-and-act as one statement, never check-then-act) — applied to a `capacity` row instead of a `jobs` row. A real defect was caught during live verification, not by unit tests: job creation originally dispatched its `job.queued` Kafka event immediately (V0.2 behavior), before any reservation could exist — a fast worker's claim was correctly rejected but the Kafka offset advanced with no redelivery, permanently stranding admitted-but-never-claimed jobs. Fixed by moving dispatch from job-creation time to admission time: the Scheduler now writes the outbox row in the same transaction as the reservation (mirroring Recovery's existing retry-dispatch pattern), never a synchronous Kafka call. This claim is directly evidence-backed, not asserted: `test_admission_never_calls_kafka_directly` proves no Kafka reference exists in the scheduler module; `test_reservation_and_outbox_survive_a_simulated_scheduler_crash` proves a crashed Scheduler never strands a job, because a wholly separate, uncoordinated Outbox Relay process can independently discover and publish the durable row. No exactly-once claims, no per-node/Kubernetes/Ray/Slurm claims anywhere in V0.4 documentation.

## Architectural debt (tracked, not fixed now)
- `app/repository/outbox.py::insert_event` performs its own internal `db.commit()`, which is what actually closes the reservation+outbox transaction in `app/services/scheduler.py::try_admit` — correct today (nothing commits before it), but implicit rather than an explicit transaction boundary the caller controls. Revisit when this code is next refactored: prefer an explicit `with db.begin():`-style boundary so a future added repository call can't accidentally commit early or split a transaction that must stay atomic.

## Explicitly deferred (not implemented, not claimed) — updated for V0.4
- Kubernetes, Ray, Slurm, Redis
- Per-node placement / bin-packing / GPU topology awareness
- Multi-tenant quotas, weighted-fair-scheduling (ADR 008 — revisit once a real tenant concept exists)
- Preemption of running jobs
- Autoscaling
- Reservation-expiry timers (ADR 009 — unclaimed-but-reserved is a worker-capacity problem, not solved by a timeout)
- DLQ redrive/reprocessing tooling
- Authentication / authorization

## Future versions (not started)
V0.5 ML lifecycle (dataset/model/artifact lineage), V0.6 post-training (SFT/DPO/GRPO), V0.7 evaluation, V0.8 release mgmt, V0.9 observability, V1.0 production simulation.

## Rule
No row marked "Done" without a corresponding artifact (test output, CI run link, or doc file) — no self-certified checkmarks.
