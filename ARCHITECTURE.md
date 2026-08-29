# ARCHITECTURE — V0.1

## Scope note
This describes V0.1 only, plus explicit seams for future versions. Full target architecture (Kafka, K8s, workers, registries) lives in project vision, not implemented here.

## Control plane vs data plane
- **Control plane (V0.1 = entire system):** FastAPI service + Postgres. Owns job metadata, lifecycle state, API contracts. This is the only thing that exists in V0.1.
- **Data plane (future, not built yet):** Workers, GPU/CPU pool, artifact store. V0.1 defines the `jobs` table and API shape so a future worker can poll/claim jobs without schema rework — but no worker exists yet.

## Components (V0.1)
```
Client (curl/tests)
      |
      v
FastAPI app (single process)
   |-- routers/jobs.py     (HTTP handlers, no business logic)
   |-- services/jobs.py    (validation, status transition rules)
   |-- repository/jobs.py  (all SQL, one place)
   |-- health.py           (healthz/readyz)
      |
      v
Postgres (single `jobs` table)
```

## Service boundaries
- One process, one deployable unit for V0.1 (no microservices yet — premature at this scope).
- Internal layering (router -> service -> repository) exists so V0.2 can insert a queue/worker between service and repository without rewriting HTTP layer.

## Database ownership
- API service is sole owner/writer of `jobs` table in V0.1.
- Schema designed to anticipate V0.2 fields (not added yet, just not blocked): `worker_id`, `attempt_count`, `queued_at` — documented in ADR 001, added only when V0.2 actually needs them (no speculative columns added now).

## Job lifecycle (conceptual, NOT enforced in V0.1)
Intended states, defined now so no code path assumes contradictory semantics later:
```
PENDING -> QUEUED -> RUNNING -> SUCCEEDED
                              -> FAILED
                              -> CANCELLED
```
`create_job()` always produces `PENDING`. No other code path sets a job to any other status in V0.1 — PATCH can technically set any value (no enforcement), but application code itself never does. Transition enforcement is a V0.2 deliverable.

## API contract
- REST, JSON, `/v1` prefix.
- Job resource: `{id, type, status, config, created_at, updated_at}`.
- Status enum for V0.1: `PENDING` only at creation; PATCH can set any value but no state-machine enforcement yet (real state machine arrives V0.2 — documented as known gap, not silently omitted).

## Configuration
- All config via env vars: `DATABASE_URL`, `LOG_LEVEL`, `PORT`.
- No secrets committed; `.env.example` provided, real `.env` gitignored.

## Security boundary (V0.1)
- No auth. Service assumed to run on trusted local/docker network only.
- Documented as a known gap — auth is out of scope until a version explicitly adds it (do not silently add partial auth now).

## Failure modes considered
- DB unreachable at startup -> app should still start, `/readyz` reports 503 (don't crash-loop on transient DB delay in compose).
- DB connection lost mid-request -> handler returns 5xx, no silent data loss (no in-memory queue masking failures).
- Duplicate job creation requests -> V0.1 does not dedupe (idempotency is explicitly a V0.3 concern) — acceptable because no execution side-effects exist yet.

## Observability (V0.1 minimum)
- Structured JSON logs only (request id, method, path, status, latency_ms).
- No Prometheus/Grafana yet (that's V0.9) — logs are sufficient at this scope.

## Testing strategy
- Unit: service-layer validation logic, no DB.
- Integration: repository + API against real Postgres via docker-compose in CI (not mocked) — mocking DB was explicitly rejected because it hides schema/query bugs.

## Local development
- `docker compose up --build` — API + Postgres only.
- Hot-reload for API dev optional, not required for V0.1 done-criteria.

## Future scalability seams (not built now)
- Repository layer isolates SQL so Postgres could later be sharded/replaced without touching handlers.
- API versioning (`/v1`) leaves room for `/v2` when job schema changes for scheduling.
- Layering leaves an obvious slot for a queue client between service and repository in V0.2.
