import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import JobCreate, JobList, JobOut, JobUpdate
from app.services import jobs as service

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=201)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    return service.create_job(db, payload)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.get_job(db, job_id)


@router.get("", response_model=JobList)
def list_jobs(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = service.list_jobs(db, limit, offset)
    return JobList(items=items, limit=limit, offset=offset, total=total)


@router.patch("/{job_id}", response_model=JobOut)
def update_job(job_id: uuid.UUID, payload: JobUpdate, db: Session = Depends(get_db)):
    return service.update_job(db, job_id, payload)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    service.delete_job(db, job_id)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.cancel_job(db, job_id)
