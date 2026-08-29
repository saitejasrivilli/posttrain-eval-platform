import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.repository import artifacts as artifacts_repo
from app.storage import hash_bytes, object_exists_with_hash, upload as storage_upload


def upload_artifact(
    db: Session,
    storage_client,
    data: bytes,
    artifact_type: str,
    uploader_id: str,
    job_id: uuid.UUID | None = None,
    attempt_number: int | None = None,
):
    """Full upload flow (ARTIFACT_LIFECYCLE_V0.5.md): dedupe by content hash,
    metadata-first PENDING row, claim upload lease, upload bytes, verify hash,
    fencing-conditioned flip to UPLOADED. Idempotent: uploading identical
    content again returns the existing (already UPLOADED) artifact without
    re-uploading (FAILURE_SCENARIOS_V0.5.md #5)."""
    content_hash = hash_bytes(data)

    existing = artifacts_repo.get_by_hash(db, content_hash)
    if existing is not None and existing.status == "UPLOADED":
        return existing  # dedup: identical bytes already present

    artifact = existing or artifacts_repo.create_pending(
        db, content_hash, artifact_type, job_id=job_id, attempt_number=attempt_number
    )

    claimed = artifacts_repo.claim_upload_lease(
        db, artifact.id, uploader_id, settings.upload_lease_duration_seconds
    )
    if claimed is None:
        raise HTTPException(
            status_code=409,
            detail={"message": "artifact upload already in progress or not PENDING", "artifact_id": str(artifact.id)},
        )

    storage_upload(storage_client, claimed.storage_key, data)

    if not object_exists_with_hash(storage_client, claimed.storage_key, content_hash):
        # Upload didn't verify -- leave PENDING (Reconciler's grace-period/
        # lease-expiry sweep will resolve it; do not guess here).
        raise HTTPException(status_code=502, detail="artifact upload could not be verified")

    finalized = artifacts_repo.mark_uploaded(db, claimed.id, uploader_id, size_bytes=len(data))
    if finalized is None:
        # Fenced out -- our lease lapsed mid-upload and someone/something else
        # already acted on this row. Discard our result, do not retry here.
        raise HTTPException(status_code=409, detail="upload lease expired before completion; retry")
    return finalized


def get_uploaded_or_404(db: Session, artifact_id: uuid.UUID):
    artifact = artifacts_repo.get(db, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    if artifact.status != "UPLOADED":
        raise HTTPException(
            status_code=409,
            detail={"message": "artifact is not UPLOADED", "status": artifact.status},
        )
    return artifact
