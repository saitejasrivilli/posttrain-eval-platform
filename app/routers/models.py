import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import ModelCreate, ModelOut, ModelVersionCreate, ModelVersionList, ModelVersionOut
from app.services import lineage as lineage_service
from app.services import models as service

router = APIRouter(prefix="/v1/models", tags=["models"])


@router.post("", response_model=ModelOut, status_code=201)
def create_model(payload: ModelCreate, db: Session = Depends(get_db)):
    return service.create_model(db, payload.name, payload.description)


@router.get("/{model_id}", response_model=ModelOut)
def get_model(model_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.get_model_or_404(db, model_id)


@router.post("/{model_id}/versions", response_model=ModelVersionOut, status_code=201)
def register_model_version(model_id: uuid.UUID, payload: ModelVersionCreate, db: Session = Depends(get_db)):
    return service.register_version(db, model_id, payload.artifact_id, payload.training_run_id)


@router.get("/{model_id}/versions", response_model=ModelVersionList)
def list_model_versions(
    model_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = service.list_versions(db, model_id, limit, offset)
    return ModelVersionList(items=items, limit=limit, offset=offset, total=total)


@router.get("/{model_id}/versions/{version_number}", response_model=ModelVersionOut)
def get_model_version(model_id: uuid.UUID, version_number: int, db: Session = Depends(get_db)):
    return service.get_version_or_404(db, model_id, version_number)


@router.get("/{model_id}/versions/{version_number}/lineage")
def get_model_version_lineage(model_id: uuid.UUID, version_number: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException

    result = lineage_service.get_lineage(db, model_id, version_number)
    if result is None:
        raise HTTPException(status_code=404, detail="model version not found")

    def _out(obj):
        if obj is None:
            return None
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    return {k: _out(v) for k, v in result.items()}
