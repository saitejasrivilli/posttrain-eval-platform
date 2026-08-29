# Job State Transition Documentation — V0.2

## States (unchanged names from V0.1's conceptual doc, now enforced)
`PENDING`, `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`

## Valid transitions (enforced at repository layer)

| From | To | Trigger | Actor |
|---|---|---|---|
| (none) | PENDING | job created | API |
| PENDING | QUEUED | job created (V0.2 auto-queues immediately, no separate queueing decision yet) | API |
| QUEUED | RUNNING | worker claims job | Worker (conditional UPDATE) |
| QUEUED | CANCELLED | user cancels before claim | API |
| RUNNING | SUCCEEDED | execution completes without error | Worker |
| RUNNING | FAILED | execution raises | Worker |
| RUNNING | CANCELLED | worker observes `cancel_requested` flag at checkpoint | Worker |

## Invalid transitions (must be rejected, not silently accepted)
Every transition not in the table above, including but not limited to:
- `PENDING -> RUNNING` (skips QUEUED, no worker claim happened)
- `PENDING -> SUCCEEDED` / `PENDING -> FAILED` (no execution occurred)
- `SUCCEEDED -> *` (terminal state, immutable)
- `FAILED -> *` (terminal state, immutable — retry engine reusing a job record is explicitly V0.3, not a V0.2 transition)
- `CANCELLED -> *` (terminal state, immutable)
- `RUNNING -> QUEUED` (no "return to queue" concept in V0.2)

Rejection mechanism: repository-layer conditional UPDATE (`WHERE status = <expected_from>`) returns rowcount 0 -> service layer raises `409 Conflict` with the attempted and actual status in the response body. This is enforced by the same SQL primitive used for concurrency-safe claiming (see ADR 002) — transition legality and race-safety are the same mechanism, not two separate checks.

## Terminal states
`SUCCEEDED`, `FAILED`, `CANCELLED` — no transitions out. A job needing to run again in V0.2 requires creating a new job record (no in-place resubmission until a later version explicitly adds it).

## Relationship to V0.1
V0.1 explicitly documented this lifecycle as *not enforced* (ARCHITECTURE.md, ADR 001 decision 3). V0.2 is the version ADR 001 named as where enforcement would arrive. No V0.1 claim is contradicted — V0.1 never claimed transitions were valid, only that the field existed.

## Cancellation is cooperative, not immediate
`POST /v1/jobs/{id}/cancel` does not stop a running workload the instant it's called. It sets `cancel_requested=true` and returns immediately; the actual `RUNNING -> CANCELLED` transition only happens when the worker reaches a defined checkpoint and observes the flag. Callers must not assume the workload has stopped when the endpoint returns 200 — they must poll job status to observe the eventual `CANCELLED` transition, same as any other async completion. This must be stated plainly in the API docs/README, not left implicit, because the endpoint name ("cancel") otherwise invites the wrong assumption of instant preemption.

## Cancellation is not a transition from every state
Cancellation is only a fast-path atomic transition from `PENDING`/`QUEUED`. From `RUNNING` it is a *request* (`cancel_requested` flag), not an immediate transition — the actual `RUNNING -> CANCELLED` transition still only happens via the worker's checkpoint check, keeping the state machine's actor model consistent (only the worker transitions jobs out of `RUNNING`, ever).
