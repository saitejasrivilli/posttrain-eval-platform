import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus
from app.repository import jobs as repo
from app.schemas import JobCreate, JobUpdate


def create_job(db: Session, payload: JobCreate) -> Job:
    job = Job(job_type=payload.job_type, status=JobStatus.PENDING.value, config=payload.config)
    return repo.insert(db, job)


def get_job(db: Session, job_id: uuid.UUID) -> Job:
    job = repo.get(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def list_jobs(db: Session, limit: int, offset: int) -> tuple[list[Job], int]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    return repo.list_(db, limit, offset)


def update_job(db: Session, job_id: uuid.UUID, payload: JobUpdate) -> Job:
    job = get_job(db, job_id)
    if payload.status is not None:
        job.status = payload.status
    if payload.config is not None:
        job.config = payload.config
    return repo.update(db, job)


def delete_job(db: Session, job_id: uuid.UUID) -> None:
    job = get_job(db, job_id)
    repo.soft_delete(db, job)
