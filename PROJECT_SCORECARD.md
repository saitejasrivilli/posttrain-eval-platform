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

## Future versions (not started)
V0.3 failure recovery (heartbeat, lease, stale-job detector, retry policy, DLQ), V0.4 scheduling, V0.5 ML lifecycle, V0.6 post-training, V0.7 evaluation, V0.8 release mgmt, V0.9 observability, V1.0 production simulation.

## Rule
No row marked "Done" without a corresponding artifact (test output, CI run link, or doc file) — no self-certified checkmarks.
