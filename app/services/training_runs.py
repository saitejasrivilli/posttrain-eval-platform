import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repository import datasets as datasets_repo
from app.repository import models as models_repo
from app.repository import training_runs as training_runs_repo
from app.schemas import JobCreate
from app.services import jobs as jobs_service


def create_training_run(
    db: Session,
    job_type: str,
    dataset_id: uuid.UUID,
    dataset_version_number: int,
    training_config: dict,
    code_commit: str,
    container_image: str,
    base_model_id: uuid.UUID | None = None,
    base_model_version_number: int | None = None,
    random_seed: int | None = None,
    priority: int = 50,
    job_config: dict | None = None,
):
    """Creates a job via the existing, UNMODIFIED V0.2 job-creation path, plus
    a training_runs row referencing it (ARCHITECTURE_V0.5.md). The job then
    proceeds through the existing V0.2/V0.3/V0.4 pipeline untouched."""
    dataset_version = datasets_repo.get_version(db, dataset_id, dataset_version_number)
    if dataset_version is None:
        raise HTTPException(status_code=404, detail="dataset version not found")

    if base_model_id is not None:
        base_version = models_repo.get_version(db, base_model_id, base_model_version_number)
        if base_version is None:
            raise HTTPException(status_code=404, detail="base model version not found")

    job = jobs_service.create_job(
        db, JobCreate(job_type=job_type, config=job_config, priority=priority)
    )

    return training_runs_repo.create(
        db,
        job_id=job.id,
        dataset_id=dataset_id,
        dataset_version_number=dataset_version_number,
        training_config=training_config,
        code_commit=code_commit,
        container_image=container_image,
        base_model_id=base_model_id,
        base_model_version_number=base_model_version_number,
        random_seed=random_seed,
    )


def get_training_run_or_404(db: Session, training_run_id: uuid.UUID):
    run = training_runs_repo.get(db, training_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="training run not found")
    return run
