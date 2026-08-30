import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.repository import checkpoints as checkpoints_repo
from app.repository import training_metrics as metrics_repo
from app.repository import training_run_outputs as outputs_repo
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


@router.get("/{training_run_id}/checkpoints")
def list_checkpoints(training_run_id: uuid.UUID, db: Session = Depends(get_db)):
    service.get_training_run_or_404(db, training_run_id)
    checkpoints = checkpoints_repo.list_for_run(db, training_run_id)
    return [
        {
            "training_run_id": str(c.training_run_id),
            "attempt_number": c.attempt_number,
            "step": c.step,
            "artifact_id": str(c.artifact_id),
            "checkpoint_format_version": c.checkpoint_format_version,
            "created_at": c.created_at,
        }
        for c in checkpoints
    ]


@router.get("/{training_run_id}/metrics")
def list_metrics(
    training_run_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    service.get_training_run_or_404(db, training_run_id)
    items, total = metrics_repo.list_for_run(db, training_run_id, limit, offset)
    return {
        "items": [
            {
                "step": m.step, "loss": m.loss, "learning_rate": m.learning_rate,
                "gpu_memory_allocated_mb": m.gpu_memory_allocated_mb, "recorded_at": m.recorded_at,
            }
            for m in items
        ],
        "limit": limit, "offset": offset, "total": total,
    }


@router.get("/{training_run_id}/output")
def get_output(training_run_id: uuid.UUID, db: Session = Depends(get_db)):
    service.get_training_run_or_404(db, training_run_id)
    output = outputs_repo.get(db, training_run_id)
    if output is None:
        raise HTTPException(status_code=404, detail="training run has not completed successfully yet")
    return {
        "training_run_id": str(output.training_run_id),
        "final_artifact_id": str(output.final_artifact_id),
        "attempt_number": output.attempt_number,
        "created_at": output.created_at,
    }
