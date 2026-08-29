import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repository import artifacts as artifacts_repo
from app.repository import models as models_repo


def create_model(db: Session, name: str, description: str | None):
    return models_repo.create(db, name, description)


def get_model_or_404(db: Session, model_id: uuid.UUID):
    model = models_repo.get(db, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="model not found")
    return model


def register_version(db: Session, model_id: uuid.UUID, artifact_id: uuid.UUID, training_run_id: uuid.UUID | None):
    """The explicit, separate registration step (REQUIREMENTS_V0.5.md,
    STATE_TRANSITIONS_V0.5.md #2). Never triggered automatically by training
    success -- always this call."""
    get_model_or_404(db, model_id)

    artifact = artifacts_repo.get(db, artifact_id)
    if artifact is None or artifact.status != "UPLOADED":
        # Hard invariant (ARTIFACT_LIFECYCLE_V0.5.md): never register a
        # non-UPLOADED artifact. Re-verified here, not trusted from the caller.
        raise HTTPException(
            status_code=409,
            detail={"message": "artifact is not UPLOADED, cannot register", "artifact_id": str(artifact_id)},
        )

    existing = models_repo.get_version_by_artifact(db, artifact_id)
    if existing is not None:
        # FAILURE_SCENARIOS_V0.5.md #6: duplicate model registration -- unlike
        # dataset versions (ADR 010), an artifact maps to at most one model version.
        raise HTTPException(
            status_code=409,
            detail={
                "message": "artifact already registered as a model version",
                "model_id": str(existing.model_id),
                "version_number": existing.version_number,
            },
        )

    return models_repo.create_version(db, model_id, artifact_id, training_run_id)


def list_versions(db: Session, model_id: uuid.UUID, limit: int, offset: int):
    get_model_or_404(db, model_id)
    return models_repo.list_versions(db, model_id, limit, offset)


def get_version_or_404(db: Session, model_id: uuid.UUID, version_number: int):
    version = models_repo.get_version(db, model_id, version_number)
    if version is None:
        raise HTTPException(status_code=404, detail="model version not found")
    return version
