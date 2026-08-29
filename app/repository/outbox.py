import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401 -- registers Job so Outbox's FK to jobs.id resolves
from app.models.outbox import Outbox


def insert_event(db: Session, job_id: uuid.UUID, event_type: str) -> Outbox:
    row = Outbox(job_id=job_id, event_type=event_type, payload={"job_id": str(job_id)})
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_unpublished(db: Session, limit: int = 100) -> list[Outbox]:
    return (
        db.query(Outbox)
        .filter(Outbox.published_at.is_(None))
        .order_by(Outbox.created_at.asc())
        .limit(limit)
        .all()
    )


def mark_published(db: Session, outbox_id: uuid.UUID) -> None:
    row = db.query(Outbox).filter(Outbox.id == outbox_id).one_or_none()
    if row is None:
        return
    row.published_at = datetime.now(timezone.utc)
    db.add(row)
    db.commit()
