import uuid

from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401
from app.models.scheduling_decision import SchedulingDecision


def insert(
    db: Session,
    job_id: uuid.UUID,
    decision: str,
    reason: str,
    requested_cpu: int,
    requested_memory_mb: int,
    requested_gpu: int,
    available_cpu_snapshot: int,
    available_memory_mb_snapshot: int,
    available_gpu_snapshot: int,
    effective_priority: float,
) -> SchedulingDecision:
    row = SchedulingDecision(
        job_id=job_id,
        decision=decision,
        reason=reason,
        requested_cpu=requested_cpu,
        requested_memory_mb=requested_memory_mb,
        requested_gpu=requested_gpu,
        available_cpu_snapshot=available_cpu_snapshot,
        available_memory_mb_snapshot=available_memory_mb_snapshot,
        available_gpu_snapshot=available_gpu_snapshot,
        effective_priority=effective_priority,
    )
    db.add(row)
    db.flush()
    return row


def list_for_job(db: Session, job_id: uuid.UUID) -> list[SchedulingDecision]:
    return (
        db.query(SchedulingDecision)
        .filter(SchedulingDecision.job_id == job_id)
        .order_by(SchedulingDecision.decided_at.asc())
        .all()
    )
