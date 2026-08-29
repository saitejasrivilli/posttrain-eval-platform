import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dataset import Dataset, DatasetVersion


def create(db: Session, name: str, description: str | None) -> Dataset:
    dataset = Dataset(name=name, description=description, created_at=datetime.now(timezone.utc))
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


def get(db: Session, dataset_id: uuid.UUID) -> Dataset | None:
    return db.query(Dataset).filter(Dataset.id == dataset_id).one_or_none()


def next_version_number(db: Session, dataset_id: uuid.UUID) -> int:
    current_max = (
        db.query(func.max(DatasetVersion.version_number))
        .filter(DatasetVersion.dataset_id == dataset_id)
        .scalar()
    )
    return (current_max or 0) + 1


def create_version(db: Session, dataset_id: uuid.UUID, artifact_id: uuid.UUID) -> DatasetVersion:
    """ADR 010: a NEW version row every call, even for identical content
    (the artifact layer already dedupes bytes -- this only creates a new
    registration EVENT). Caller must have already confirmed artifact_id's
    status is UPLOADED (the hard invariant, ARTIFACT_LIFECYCLE_V0.5.md)."""
    version_number = next_version_number(db, dataset_id)
    version = DatasetVersion(
        dataset_id=dataset_id,
        version_number=version_number,
        artifact_id=artifact_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def get_version(db: Session, dataset_id: uuid.UUID, version_number: int) -> DatasetVersion | None:
    return (
        db.query(DatasetVersion)
        .filter(DatasetVersion.dataset_id == dataset_id, DatasetVersion.version_number == version_number)
        .one_or_none()
    )


def list_versions(db: Session, dataset_id: uuid.UUID, limit: int, offset: int) -> tuple[list[DatasetVersion], int]:
    base = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id)
    total = base.count()
    items = base.order_by(DatasetVersion.version_number.desc()).limit(limit).offset(offset).all()
    return items, total
