import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.job import Job


def insert(db: Session, job: Job) -> Job:
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get(db: Session, job_id: uuid.UUID) -> Job | None:
    return (
        db.query(Job)
        .filter(Job.id == job_id, Job.deleted_at.is_(None))
        .one_or_none()
    )


def list_(db: Session, limit: int, offset: int) -> tuple[list[Job], int]:
    base = db.query(Job).filter(Job.deleted_at.is_(None))
    total = base.with_entities(func.count(Job.id)).scalar()
    items = base.order_by(Job.created_at.desc()).limit(limit).offset(offset).all()
    return items, total


def update(db: Session, job: Job) -> Job:
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def soft_delete(db: Session, job: Job) -> Job:
    job.deleted_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
