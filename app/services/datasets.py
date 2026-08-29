import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repository import datasets as datasets_repo
from app.services import artifacts as artifacts_service


def create_dataset(db: Session, name: str, description: str | None):
    return datasets_repo.create(db, name, description)


def get_dataset_or_404(db: Session, dataset_id: uuid.UUID):
    dataset = datasets_repo.get(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


def create_dataset_version(db: Session, storage_client, dataset_id: uuid.UUID, data: bytes, uploader_id: str):
    get_dataset_or_404(db, dataset_id)
    # ADR 010: uploading identical content still creates a NEW version row
    # (the registration event is meaningful even when bytes are deduped by
    # the artifact layer) -- deliberately asymmetric with model registration.
    artifact = artifacts_service.upload_artifact(db, storage_client, data, "DATASET", uploader_id)
    if artifact.status != "UPLOADED":
        raise HTTPException(status_code=502, detail="dataset artifact did not reach UPLOADED")
    return datasets_repo.create_version(db, dataset_id, artifact.id)


def list_versions(db: Session, dataset_id: uuid.UUID, limit: int, offset: int):
    get_dataset_or_404(db, dataset_id)
    return datasets_repo.list_versions(db, dataset_id, limit, offset)


def get_version_or_404(db: Session, dataset_id: uuid.UUID, version_number: int):
    version = datasets_repo.get_version(db, dataset_id, version_number)
    if version is None:
        raise HTTPException(status_code=404, detail="dataset version not found")
    return version
