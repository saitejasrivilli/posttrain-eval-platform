# REQUIREMENTS — V0.1 Foundation

## Purpose
Stand up minimal control-plane skeleton (API + DB + CI) that later versions (job system, scheduling, ML lifecycle) build on without rework.

## Functional requirements
1. Job metadata API (no execution engine yet)
   - `POST /jobs` — create job record (id, type, status=PENDING, config JSON, created_at)
   - `GET /jobs/{id}` — fetch job by id
   - `GET /jobs` — list jobs (pagination: limit/offset)
   - `PATCH /jobs/{id}` — update status/config (used later by workers; stubbed now)
   - `DELETE /jobs/{id}` — soft-delete (tombstone, not hard delete — future audit needs)
2. Health/readiness
   - `GET /healthz` — process alive, no dependency checks
   - `GET /readyz` — checks DB connectivity, returns 503 if DB unreachable
3. Persistence
   - Postgres, single `jobs` table for V0.1
   - Migrations via versioned SQL or Alembic (pick one, document in ADR)
4. Local dev
   - `docker compose up` brings up API + Postgres, no external deps
5. CI
   - GitHub Actions: lint, unit tests, integration tests (against ephemeral Postgres container) on every PR

## Non-functional requirements
- Structured JSON logging (request id, latency, status code) on every request
- Config via env vars only (12-factor), no hardcoded connection strings
- All DB access through one repository/module — no raw SQL scattered across handlers (so V0.2+ can swap/extend without churn)
- API versioned from day 1: `/v1/...` prefix

## Explicit non-goals for V0.1
- No job execution, no worker process, no queue
- No auth/authz (stub only: accept requests, no token validation)
- No scheduling logic, no priority, no resource awareness
- No dataset/model/artifact registries

## Acceptance criteria (must all pass before v0.1.0 tag)
- [ ] `docker compose up` starts API + DB with zero manual steps
- [ ] All 5 job endpoints work via curl, verified against running stack
- [ ] `GET /readyz` returns 503 when DB is down, 200 when up (test kills DB container mid-run)
- [ ] Migrations run automatically on startup (or via one documented command) against a fresh DB
- [ ] Unit tests cover: job creation validation, status transitions stub, pagination edge cases (empty, single page, last page)
- [ ] Integration test: full create -> get -> list -> patch -> delete cycle against real Postgres (not mocked)
- [ ] CI pipeline green on a clean PR
- [ ] No number/claim in README not traceable to actual test/run output
