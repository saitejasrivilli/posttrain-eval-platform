# API Changes — V0.4

All V0.1-V0.3 endpoints unchanged in contract.

## `JobCreate` schema addition
- `priority: int = 50` (0-100, validated range)
- `config.resources: {cpu, memory_mb, gpu}` -- optional; if omitted, a documented default resource request is applied (e.g. `cpu=1, memory_mb=512, gpu=0`) so every job has a well-defined resource footprint, never an implicit zero that would trivially always admit.

## `JobOut` schema additions
- `priority: int`
- `effective_priority: float | None` -- computed at read time (SCHEDULING_POLICY_V0.4.md's formula), `None` if the job isn't currently in the schedulable set (e.g. already running or terminal) -- this is a derived/informational field, not stored.

## New read-only endpoint
`GET /v1/jobs/{id}/scheduling-decisions` -- full history of `ADMITTED`/`WAITING` decisions for a job, same "read-only, no new write path" posture as V0.3's `GET /v1/jobs/{id}/attempts`.

## New read-only endpoint
`GET /v1/capacity` -- current cluster capacity: total/allocated/available per resource dimension. Answers the "resource utilization tracking" requirement directly.

## No changes
Every V0.1-V0.3 endpoint's contract is unchanged. `POST /v1/jobs/{id}/cancel` behavior is unchanged in shape -- its effect on scheduling (a cancelled job is never admitted going forward, and a `RUNNING` job's `cancel_requested` flag is what the Scheduler's re-verification and Recovery's reclaim both already check) is a consequence of existing mechanisms, not a new code path in the cancel endpoint itself.
