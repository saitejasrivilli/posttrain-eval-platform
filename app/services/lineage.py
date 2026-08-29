import uuid

from sqlalchemy.orm import Session

from app.repository import artifacts as artifacts_repo
from app.repository import datasets as datasets_repo
from app.repository import jobs as jobs_repo
from app.repository import models as models_repo
from app.repository import training_runs as training_runs_repo


def get_lineage(db: Session, model_id: uuid.UUID, version_number: int) -> dict:
    """LINEAGE_MODEL_V0.5.md: fixed FK-chain join, not a graph traversal
    (ADR 012). Returns the full "how was this produced" answer."""
    model_version = models_repo.get_version(db, model_id, version_number)
    if model_version is None:
        return None

    artifact = artifacts_repo.get(db, model_version.artifact_id)
    training_run = None
    dataset = None
    dataset_version = None
    base_model_version = None
    job = None

    if model_version.training_run_id is not None:
        training_run = training_runs_repo.get(db, model_version.training_run_id)
        if training_run is not None:
            dataset_version = datasets_repo.get_version(
                db, training_run.dataset_id, training_run.dataset_version_number
            )
            dataset = datasets_repo.get(db, training_run.dataset_id)
            job = jobs_repo.get(db, training_run.job_id)
            if training_run.base_model_id is not None:
                base_model_version = models_repo.get_version(
                    db, training_run.base_model_id, training_run.base_model_version_number
                )

    return {
        "model_version": model_version,
        "artifact": artifact,
        "training_run": training_run,
        "dataset": dataset,
        "dataset_version": dataset_version,
        "base_model_version": base_model_version,
        "job": job,
    }
