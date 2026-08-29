import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.repository import attempts as attempts_repo
from app.repository import jobs as repo
from app.repository import outbox as outbox_repo
from app.retry_policy import compute_next_retry_at

logger = logging.getLogger("app")


def reclaim_stale_leases(db: Session) -> int:
    """One pass of stale-lease reclamation (ADR 004). Each job is handled by
    its own atomic reclaim UPDATE -- a crash between two jobs in this loop
    leaves the not-yet-processed one exactly as stale as before, picked up
    by the next poll (this process restarted, or another instance)."""
    reclaimed = 0
    for job in repo.list_stale_leases(db):
        next_retry_at = compute_next_retry_at(
            job.attempt_number + 1,
            settings.base_delay_seconds,
            settings.max_delay_seconds,
            settings.jitter_ratio,
        )
        result = repo.reclaim_stale(db, job.id, settings.max_attempts, next_retry_at)
        if result is None:
            continue  # another recovery process (or a heartbeat) won the race
        lost_attempt_number, new_status = result  # the just-fenced attempt's own number
        attempts_repo.mark_lost(db, job.id, lost_attempt_number, worker_id=job.lease_owner or "unknown")
        reclaimed += 1
        logger.info(
            "job_reclaimed",
            extra={
                "job_id": str(job.id),
                "lost_attempt_number": lost_attempt_number,
                "new_status": new_status,
            },
        )
    return reclaimed


def dispatch_due_retries(db: Session) -> int:
    """One pass of retry dispatch: any QUEUED job whose backoff has elapsed
    gets a fresh outbox event for its current attempt_number, then
    next_retry_at is cleared so it isn't re-dispatched every poll."""
    dispatched = 0
    for job in repo.list_retry_due(db):
        outbox_repo.insert_event(db, job.id, "job.queued")
        repo.clear_retry_dispatched(db, job.id)
        db.commit()
        dispatched += 1
        logger.info("retry_dispatched", extra={"job_id": str(job.id), "attempt_number": job.attempt_number})
    return dispatched


def run_once(db: Session) -> tuple[int, int]:
    reclaimed = reclaim_stale_leases(db)
    dispatched = dispatch_due_retries(db)
    return reclaimed, dispatched
