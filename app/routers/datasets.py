import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import DatasetCreate, DatasetOut, DatasetVersionList, DatasetVersionOut
from app.services import datasets as service
from app.storage import make_client

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


@router.post("", response_model=DatasetOut, status_code=201)
def create_dataset(payload: DatasetCreate, db: Session = Depends(get_db)):
    return service.create_dataset(db, payload.name, payload.description)


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.get_dataset_or_404(db, dataset_id)


@router.post("/{dataset_id}/versions", response_model=DatasetVersionOut, status_code=201)
async def create_dataset_version(
    dataset_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    data = await file.read()
    client = make_client()
    return service.create_dataset_version(db, client, dataset_id, data, uploader_id="api")


@router.get("/{dataset_id}/versions", response_model=DatasetVersionList)
def list_dataset_versions(
    dataset_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = service.list_versions(db, dataset_id, limit, offset)
    return DatasetVersionList(items=items, limit=limit, offset=offset, total=total)


@router.get("/{dataset_id}/versions/{version_number}", response_model=DatasetVersionOut)
def get_dataset_version(dataset_id: uuid.UUID, version_number: int, db: Session = Depends(get_db)):
    return service.get_version_or_404(db, dataset_id, version_number)
