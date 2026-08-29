import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.job import JobStatus
from app.repository import executions as executions_repo
from app.repository import jobs as repo

# V0.2 has no real training/eval executor -- that's V0.6+. This module proves
# the orchestration path (claim -> execute -> finalize) with a simulated body.
SIMULATED_FAILURE_JOB_TYPE = "simulate_failure"


def process_job_message(db: Session, job_id: uuid.UUID, worker_id: str) -> str:
    """Idempotent, concurrency-safe handling of one `job.queued` message.
    Returns a short outcome string for logging/testing:
      "claimed"        -- this call executed the job to a terminal state
      "not_claimed"    -- another worker claimed it first, or it's no longer
                          QUEUED (cancelled, already terminal, duplicate
                          delivery of an already-processed job, etc). Always
                          a safe no-op ack, per ADR 003.
    """
    claimed = repo.conditional_transition(
        db,
        job_id,
        valid_from=[JobStatus.QUEUED.value],
        to_status=JobStatus.RUNNING.value,
        extra_values={"claimed_at": datetime.now(timezone.utc)},
    )
    if claimed is None:
        return "not_claimed"

    executions_repo.insert(db, job_id, worker_id)

    # Simulated execution body (no real work in V0.2).
    outcome = (
        JobStatus.FAILED.value
        if claimed.job_type == SIMULATED_FAILURE_JOB_TYPE
        else JobStatus.SUCCEEDED.value
    )

    # Cooperative-cancellation checkpoint: re-read the job to see if a cancel
    # was requested while we were "executing". See STATE_TRANSITIONS_V0.2.md --
    # this is the only checkpoint V0.2 has (no real checkpointable work exists
    # until V0.6), so cancellation is last-checkpoint-wins.
    current = repo.get(db, job_id)
    if current is not None and current.cancel_requested:
        outcome = JobStatus.CANCELLED.value

    repo.conditional_transition(db, job_id, valid_from=[JobStatus.RUNNING.value], to_status=outcome)
    executions_repo.finalize(db, job_id, outcome)
    return "claimed"
