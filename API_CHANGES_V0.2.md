# API Changes — V0.2

All V0.1 endpoints (`POST/GET/PATCH/DELETE /v1/jobs`, `GET /healthz`, `GET /readyz`) remain unchanged in contract. Additions only.

## New endpoint
`POST /v1/jobs/{id}/cancel`
- 200 + job body if transitioned (`PENDING`/`QUEUED -> CANCELLED`, or `RUNNING` -> `cancel_requested=true`, response reflects current state and includes `cancel_requested` field)
- 404 if job doesn't exist
- 409 if job already in a terminal state (`SUCCEEDED`/`FAILED`/`CANCELLED`) — cancellation of a finished job is a no-op error, not silently accepted

## Changed behavior (not endpoint shape — a semantic tightening)
- `PATCH /v1/jobs/{id}` — V0.1 accepted any `status` string unconditionally. V0.2 routes status changes through the enforced state machine: an illegal transition now returns `409 Conflict` with `{"detail": "invalid transition", "from": "...", "to": "..."}`. This is the one intentional behavior change to an existing V0.1 endpoint, and it's the exact gap V0.1's docs flagged as deferred — not a silent contract break.
- **PATCH is not a generic state-mutation endpoint.** The client requests a desired status; the *service layer* decides legality via `service.transition(job_id, requested_status)`, which is the single owner of the state machine. The repository's conditional UPDATE is the enforcement primitive, but the service is what maps "client asked for X" to "is X reachable from current state" before ever issuing SQL. No handler, router, or repository function may set `status` directly outside this path — config/other-field updates via PATCH remain simple field writes, only `status` changes go through `transition()`.
- `JobOut` schema gains two optional fields: `cancel_requested: bool`, `claimed_at: datetime | None`. Additive, doesn't break existing clients parsing the response.

## No changes
- `GET /v1/jobs`, `GET /v1/jobs/{id}`, `DELETE /v1/jobs/{id}`, `GET /healthz`, `GET /readyz` — contracts identical to V0.1.
