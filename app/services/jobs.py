import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus
from app.repository import capacity as capacity_repo
from app.repository import jobs as repo
from app.repository import reservations as reservations_repo
from app.schemas import JobCreate, JobUpdate
from app.statemachine import sources_for


def create_job(db: Session, payload: JobCreate) -> Job:
    job = Job(
        job_type=payload.job_type,
        status=JobStatus.PENDING.value,
        config=payload.config,
        priority=payload.priority,
    )
    # Job auto-queues immediately on creation. It becomes claimable only once
    # the Scheduler admits it and dispatches a job.queued event (V0.4 --
    # see repo.create_and_enqueue's docstring for why dispatch moved here).
    return repo.create_and_enqueue(db, job, queued_status=JobStatus.QUEUED.value)


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


def transition(db: Session, job_id: uuid.UUID, to_status: str) -> Job:
    """Single owner of the job state machine. The client requests a desired
    status; this function decides whether it's reachable from the job's
    current state and performs the transition atomically. No caller may set
    `Job.status` any other way -- see API_CHANGES_V0.2.md."""
    valid_from = sources_for(to_status)
    job = repo.conditional_transition(db, job_id, valid_from, to_status)
    if job is None:
        current = repo.get(db, job_id)
        if current is None:
            raise HTTPException(status_code=404, detail="job not found")
        raise HTTPException(
            status_code=409,
            detail={
                "message": "invalid transition",
                "from": current.status,
                "to": to_status,
            },
        )
    return job


def update_job(db: Session, job_id: uuid.UUID, payload: JobUpdate) -> Job:
    if payload.status is not None:
        job = transition(db, job_id, payload.status)
    else:
        job = get_job(db, job_id)
    if payload.config is not None:
        job.config = payload.config
        job = repo.update(db, job)
    return job


def cancel_job(db: Session, job_id: uuid.UUID) -> Job:
    current = get_job(db, job_id)
    if current.status in (JobStatus.PENDING.value, JobStatus.QUEUED.value):
        # V0.4: if this job already had a reservation for its prospective
        # next attempt (Scheduler admitted it but no worker claimed it yet),
        # cancelling must release that reservation -- otherwise capacity
        # leaks for a job that will never run. Same transaction as the status
        # write (ADR 007/009's release-atomicity discipline).
        valid_from = sources_for(JobStatus.CANCELLED.value)
        job = repo.conditional_transition(
            db, job_id, valid_from, JobStatus.CANCELLED.value, commit=False
        )
        if job is None:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "invalid transition",
                    "from": current.status,
                    "to": JobStatus.CANCELLED.value,
                },
            )
        released = reservations_repo.release(db, job_id, current.attempt_number + 1)
        if released is not None:
            capacity_repo.release(db, released.cpu, released.memory_mb, released.gpu)
        db.commit()
        return job
    if current.status == JobStatus.RUNNING.value:
        job = repo.set_cancel_requested(db, job_id)
        if job is None:
            # lost the race: job left RUNNING between our read and this call
            return get_job(db, job_id)
        return job
    raise HTTPException(
        status_code=409,
        detail={
            "message": "job already in a terminal state, cannot cancel",
            "status": current.status,
        },
    )


def delete_job(db: Session, job_id: uuid.UUID) -> None:
    job = get_job(db, job_id)
    repo.soft_delete(db, job)
