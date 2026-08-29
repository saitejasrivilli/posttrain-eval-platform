import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.models.job import JobStatus
from app.repository import capacity as capacity_repo
from app.repository import jobs as repo
from app.repository import outbox as outbox_repo
from app.repository import reservations as reservations_repo
from app.repository import scheduling_decisions as decisions_repo

logger = logging.getLogger("app")


def _resource_request(job) -> tuple[int, int, int]:
    resources = (job.config or {}).get("resources", {}) if job.config else {}
    cpu = resources.get("cpu", settings.default_cpu)
    memory_mb = resources.get("memory_mb", settings.default_memory_mb)
    gpu = resources.get("gpu", settings.default_gpu)
    return cpu, memory_mb, gpu


def _effective_priority(job, now) -> float:
    """ADR 008: priority + bounded aging. queue_wait_seconds uses created_at,
    a job's total time in the system (deliberate choice for retried jobs --
    see SCHEDULING_POLICY_V0.4.md)."""
    queue_wait_seconds = (now - job.created_at).total_seconds()
    raw = job.priority + settings.aging_rate * queue_wait_seconds
    return min(raw, settings.priority_ceiling)


def rank_candidates(db: Session) -> list[tuple[float, object]]:
    """Same eligibility set claim() itself would accept (SCHEDULING_POLICY_V0.4.md
    step 1), ranked by effective_priority DESC, created_at ASC."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    candidates = repo.list_schedulable(db)
    ranked = [(_effective_priority(job, now), job) for job in candidates]
    ranked.sort(key=lambda pair: (-pair[0], pair[1].created_at))
    return ranked


def try_admit(db: Session, job) -> bool:
    """The atomic reservation transaction (ADR 007). Re-verifies eligibility,
    attempts the conditional capacity reservation, inserts the reservation
    row, and records the decision -- all in one transaction. Returns True iff
    admitted. On any failure, rolls back the ENTIRE transaction (no partial
    reservation across resource dimensions ever survives) and records a
    WAITING decision in a separate, subsequent transaction."""
    from datetime import datetime, timezone

    prospective_attempt_number = job.attempt_number + 1
    cpu, memory_mb, gpu = _resource_request(job)

    try:
        # Re-verify: same eligibility claim() checks, plus "not already reserved".
        fresh = repo.get(db, job.id)
        if fresh is None or fresh.status != JobStatus.QUEUED.value or fresh.cancel_requested:
            raise _Reject("job_no_longer_eligible")
        if fresh.next_retry_at is not None and fresh.next_retry_at > datetime.now(timezone.utc):
            raise _Reject("job_no_longer_eligible")
        if reservations_repo.has_active(db, job.id, prospective_attempt_number):
            raise _Reject("already_reserved")

        reserved = capacity_repo.try_reserve(db, cpu, memory_mb, gpu)
        if not reserved:
            cap = capacity_repo.get(db)
            reason = capacity_repo.which_dimension_insufficient(cap, cpu, memory_mb, gpu)
            raise _Reject(reason, cap)

        reservations_repo.insert(db, job.id, prospective_attempt_number, cpu, memory_mb, gpu)
        # Dispatch the job.queued event HERE, in the same transaction as the
        # reservation -- not at job-creation time. See create_and_enqueue's
        # docstring (app/repository/jobs.py) for the bug this fixes: a message
        # published before a reservation exists gets consumed-and-acked with
        # nothing to claim, and never redelivers.
        outbox_repo.insert_event(db, job.id, "job.queued")
        cap_after = capacity_repo.get(db)
        decisions_repo.insert(
            db, job.id, "ADMITTED", "resources_available",
            cpu, memory_mb, gpu,
            cap_after.total_cpu - cap_after.allocated_cpu,
            cap_after.total_memory_mb - cap_after.allocated_memory_mb,
            cap_after.total_gpu - cap_after.allocated_gpu,
            _effective_priority(job, datetime.now(timezone.utc)),
        )
        db.commit()
        logger.info("job_admitted", extra={"job_id": str(job.id), "attempt_number": prospective_attempt_number})
        return True
    except _Reject as rejection:
        db.rollback()
        cap = rejection.capacity_snapshot or capacity_repo.get(db)
        decisions_repo.insert(
            db, job.id, "WAITING", rejection.reason,
            cpu, memory_mb, gpu,
            cap.total_cpu - cap.allocated_cpu,
            cap.total_memory_mb - cap.allocated_memory_mb,
            cap.total_gpu - cap.allocated_gpu,
            _effective_priority(job, datetime.now(timezone.utc)),
        )
        db.commit()
        return False


class _Reject(Exception):
    def __init__(self, reason: str, capacity_snapshot=None):
        super().__init__(reason)
        self.reason = reason
        self.capacity_snapshot = capacity_snapshot


def run_once(db: Session) -> tuple[int, int]:
    admitted = 0
    waiting = 0
    for _, job in rank_candidates(db):
        if admitted >= settings.max_admissions_per_pass:
            waiting += 1
            continue  # reconsidered next pass; effective_priority will have grown
        if try_admit(db, job):
            admitted += 1
        else:
            waiting += 1
    return admitted, waiting
