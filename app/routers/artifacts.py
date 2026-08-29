import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.repository import artifacts as artifacts_repo
from app.schemas import ArtifactList, ArtifactOut
from app.services import artifacts as service
from app.storage import make_client

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


@router.post("", response_model=ArtifactOut, status_code=201)
async def upload_artifact(
    artifact_type: str,
    job_id: Optional[uuid.UUID] = None,
    attempt_number: Optional[int] = None,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Represents what a real training script (V0.6+) calls back to the
    platform with, to report its own output -- artifact creation is part of
    the execution body, not the generic Worker orchestrator
    (ARCHITECTURE_V0.5.md)."""
    data = await file.read()
    client = make_client()
    return service.upload_artifact(
        db, client, data, artifact_type, uploader_id="api", job_id=job_id, attempt_number=attempt_number
    )


@router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(artifact_id: uuid.UUID, db: Session = Depends(get_db)):
    from fastapi import HTTPException

    artifact = artifacts_repo.get(db, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return artifact


@router.get("", response_model=ArtifactList)
def list_artifacts(
    status: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = artifacts_repo.list_(db, status, limit, offset)
    return ArtifactList(items=items, limit=limit, offset=offset, total=total)
