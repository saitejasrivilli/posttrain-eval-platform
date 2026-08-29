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

## Future versions (not started)
V0.2 job system, V0.3 reliability, V0.4 scheduling, V0.5 ML lifecycle, V0.6 post-training, V0.7 evaluation, V0.8 release mgmt, V0.9 observability, V1.0 production simulation.

## Rule
No row marked "Done" without a corresponding artifact (test output, CI run link, or doc file) — no self-certified checkmarks.
