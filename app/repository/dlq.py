import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401
from app.models.dlq import DLQEntry


def insert(
    db: Session,
    job_id: uuid.UUID,
    last_attempt_number: int,
    last_error_message: str | None,
    last_error_classification: str,
    total_attempts: int,
    commit: bool = True,
) -> DLQEntry:
    entry = DLQEntry(
        job_id=job_id,
        moved_to_dlq_at=datetime.now(timezone.utc),
        last_attempt_number=last_attempt_number,
        last_error_message=last_error_message,
        last_error_classification=last_error_classification,
        total_attempts=total_attempts,
    )
    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
    else:
        db.flush()
    return entry


def list_(db: Session, limit: int, offset: int) -> tuple[list[DLQEntry], int]:
    base = db.query(DLQEntry)
    total = base.count()
    items = base.order_by(DLQEntry.moved_to_dlq_at.desc()).limit(limit).offset(offset).all()
    return items, total
