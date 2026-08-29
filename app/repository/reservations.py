import uuid
from datetime import datetime, timezone

from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401
from app.models.reservation import Reservation


def get(db: Session, job_id: uuid.UUID, attempt_number: int) -> Reservation | None:
    return (
        db.query(Reservation)
        .filter(Reservation.job_id == job_id, Reservation.attempt_number == attempt_number)
        .one_or_none()
    )


def has_active(db: Session, job_id: uuid.UUID, attempt_number: int) -> bool:
    return (
        db.query(Reservation)
        .filter(
            Reservation.job_id == job_id,
            Reservation.attempt_number == attempt_number,
            Reservation.status == "ACTIVE",
        )
        .one_or_none()
        is not None
    )


def insert(db: Session, job_id: uuid.UUID, attempt_number: int, cpu: int, memory_mb: int, gpu: int) -> Reservation:
    reservation = Reservation(
        job_id=job_id,
        attempt_number=attempt_number,
        cpu=cpu,
        memory_mb=memory_mb,
        gpu=gpu,
        status="ACTIVE",
        created_at=datetime.now(timezone.utc),
    )
    db.add(reservation)
    db.flush()
    return reservation


def release(db: Session, job_id: uuid.UUID, attempt_number: int) -> Reservation | None:
    """Fencing-style conditional release: WHERE status='ACTIVE'. Returns the
    released row only if THIS call won the ACTIVE->RELEASED transition --
    None means someone else already released it (or it never existed),
    which the caller must treat as "do not also decrement capacity" (see
    DB_SCHEMA_CHANGES_V0.4.md's release-idempotency clarification)."""
    stmt = (
        sa_update(Reservation)
        .where(
            Reservation.job_id == job_id,
            Reservation.attempt_number == attempt_number,
            Reservation.status == "ACTIVE",
        )
        .values(status="RELEASED", released_at=datetime.now(timezone.utc))
    )
    result = db.execute(stmt)
    if result.rowcount == 0:
        return None
    return get(db, job_id, attempt_number)


def sum_active(db: Session) -> tuple[int, int, int]:
    """Ground-truth sum, used only by the conservation-invariant test/audit --
    not on the hot path (capacity.allocated_* is maintained incrementally,
    not by re-summing at read time; see DB_SCHEMA_CHANGES_V0.4.md)."""
    from sqlalchemy import func

    row = (
        db.query(
            func.coalesce(func.sum(Reservation.cpu), 0),
            func.coalesce(func.sum(Reservation.memory_mb), 0),
            func.coalesce(func.sum(Reservation.gpu), 0),
        )
        .filter(Reservation.status == "ACTIVE")
        .one()
    )
    return row
