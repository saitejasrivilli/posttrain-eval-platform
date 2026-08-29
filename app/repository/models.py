import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.model import Model, ModelVersion


def create(db: Session, name: str, description: str | None) -> Model:
    model = Model(name=name, description=description, created_at=datetime.now(timezone.utc))
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def get(db: Session, model_id: uuid.UUID) -> Model | None:
    return db.query(Model).filter(Model.id == model_id).one_or_none()


def next_version_number(db: Session, model_id: uuid.UUID) -> int:
    current_max = (
        db.query(func.max(ModelVersion.version_number)).filter(ModelVersion.model_id == model_id).scalar()
    )
    return (current_max or 0) + 1


def get_version_by_artifact(db: Session, artifact_id: uuid.UUID) -> ModelVersion | None:
    """Duplicate-model-registration check (FAILURE_SCENARIOS_V0.5.md #6):
    an artifact maps to at most one ModelVersion -- unique index on
    model_versions.artifact_id is the structural backstop; this is the
    friendly pre-check."""
    return db.query(ModelVersion).filter(ModelVersion.artifact_id == artifact_id).one_or_none()


def create_version(
    db: Session, model_id: uuid.UUID, artifact_id: uuid.UUID, training_run_id: uuid.UUID | None
) -> ModelVersion:
    version_number = next_version_number(db, model_id)
    version = ModelVersion(
        model_id=model_id,
        version_number=version_number,
        artifact_id=artifact_id,
        training_run_id=training_run_id,
        registered_at=datetime.now(timezone.utc),
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def get_version(db: Session, model_id: uuid.UUID, version_number: int) -> ModelVersion | None:
    return (
        db.query(ModelVersion)
        .filter(ModelVersion.model_id == model_id, ModelVersion.version_number == version_number)
        .one_or_none()
    )


def list_versions(db: Session, model_id: uuid.UUID, limit: int, offset: int) -> tuple[list[ModelVersion], int]:
    base = db.query(ModelVersion).filter(ModelVersion.model_id == model_id)
    total = base.count()
    items = base.order_by(ModelVersion.version_number.desc()).limit(limit).offset(offset).all()
    return items, total
