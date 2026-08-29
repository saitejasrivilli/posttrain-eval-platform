import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import TrainingRunCreate, TrainingRunOut
from app.services import training_runs as service

router = APIRouter(prefix="/v1/training-runs", tags=["training-runs"])


@router.post("", response_model=TrainingRunOut, status_code=201)
def create_training_run(payload: TrainingRunCreate, db: Session = Depends(get_db)):
    return service.create_training_run(
        db,
        job_type=payload.job_type,
        dataset_id=payload.dataset_id,
        dataset_version_number=payload.dataset_version_number,
        training_config=payload.training_config,
        code_commit=payload.code_commit,
        container_image=payload.container_image,
        base_model_id=payload.base_model_id,
        base_model_version_number=payload.base_model_version_number,
        random_seed=payload.random_seed,
        priority=payload.priority,
        job_config=payload.job_config,
    )


@router.get("/{training_run_id}", response_model=TrainingRunOut)
def get_training_run(training_run_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.get_training_run_or_404(db, training_run_id)
