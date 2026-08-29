# API Changes — V0.3

All V0.1/V0.2 endpoints unchanged in contract. V0.3 is almost entirely internal (worker/recovery-process behavior); the API surface barely moves.

## `JobOut` schema additions
- `attempt_number: int`
- `lease_owner: str | None`
- `lease_expires_at: datetime | None`
- `next_retry_at: datetime | None`

Additive only -- existing clients parsing the V0.2 response shape are unaffected.

## New read-only endpoint
`GET /v1/jobs/{id}/attempts` -- lists all `attempts` rows for a job (full retry history: which worker, which attempt, outcome, error, classification). Read-only, no new write path. Useful for the exact debugging question REQUIREMENTS_V0.3.md poses ("what job, which attempt, why, which worker, when").

## New read-only endpoint
`GET /v1/dlq` -- lists jobs currently in the dead-letter queue (paginated, same `limit`/`offset` convention as `GET /v1/jobs`). Read-only in V0.3 -- no redrive/retry-from-DLQ endpoint yet (explicitly future scope, noted in ADR 005).

## No changes
`POST /v1/jobs`, `GET /v1/jobs`, `GET /v1/jobs/{id}`, `PATCH /v1/jobs/{id}`, `DELETE /v1/jobs/{id}`, `POST /v1/jobs/{id}/cancel`, `GET /healthz`, `GET /readyz` -- contracts identical to V0.2. Note: `PATCH .../status` still routes through the same state-machine `transition()` from V0.2 (API_CHANGES_V0.2.md); it is not fencing-conditioned since API-driven transitions never hold a worker lease -- fencing only applies to `RUNNING`-sourced writes made by a Worker or Recovery process, which the API is neither.
