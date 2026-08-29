import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401 -- registers Job so Artifact's FK resolves
from app.models.artifact import Artifact
from app.storage import hash_bytes, storage_key_for_hash


def get(db: Session, artifact_id: uuid.UUID) -> Artifact | None:
    return db.query(Artifact).filter(Artifact.id == artifact_id).one_or_none()


def get_by_hash(db: Session, content_hash: str) -> Artifact | None:
    return db.query(Artifact).filter(Artifact.content_hash == content_hash).one_or_none()


def create_pending(
    db: Session, content_hash: str, artifact_type: str, job_id: uuid.UUID | None = None,
    attempt_number: int | None = None,
) -> Artifact:
    """Metadata-first (ADR 013): the row is created BEFORE any upload begins,
    with its identity (content_hash, storage_key) already known -- content
    addressing (ADR 011) means the key is derivable without uploading anything."""
    artifact = Artifact(
        content_hash=content_hash,
        storage_key=storage_key_for_hash(content_hash),
        artifact_type=artifact_type,
        job_id=job_id,
        attempt_number=attempt_number,
        status="PENDING",
        created_at=datetime.now(timezone.utc),
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def claim_upload_lease(db: Session, artifact_id: uuid.UUID, uploader_id: str, lease_duration_seconds: int) -> Artifact | None:
    """ADR 013/004: an uploader must claim the lease before uploading, so the
    Reconciler can tell "actively being uploaded" from "abandoned." Same
    atomic conditional-UPDATE primitive as jobs.claim()."""
    now = datetime.now(timezone.utc)
    stmt = (
        sa_update(Artifact)
        .where(
            Artifact.id == artifact_id,
            Artifact.status == "PENDING",
            (Artifact.upload_lease_expires_at.is_(None)) | (Artifact.upload_lease_expires_at < now),
        )
        .values(
            uploader_id=uploader_id,
            upload_lease_expires_at=now + timedelta(seconds=lease_duration_seconds),
        )
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount == 0:
        return None
    return get(db, artifact_id)


def renew_upload_lease(db: Session, artifact_id: uuid.UUID, uploader_id: str, lease_duration_seconds: int) -> bool:
    stmt = (
        sa_update(Artifact)
        .where(Artifact.id == artifact_id, Artifact.status == "PENDING", Artifact.uploader_id == uploader_id)
        .values(upload_lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_duration_seconds))
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount > 0


def mark_uploaded(db: Session, artifact_id: uuid.UUID, uploader_id: str, size_bytes: int) -> Artifact | None:
    """Fencing-conditioned on uploader_id (ADR 004 pattern): a caller whose
    lease already lapsed and was reclaimed cannot flip this row -- its result
    must be discarded, never retried."""
    stmt = (
        sa_update(Artifact)
        .where(Artifact.id == artifact_id, Artifact.status == "PENDING", Artifact.uploader_id == uploader_id)
        .values(
            status="UPLOADED",
            size_bytes=size_bytes,
            uploaded_at=datetime.now(timezone.utc),
            upload_lease_expires_at=None,
        )
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount == 0:
        return None
    return get(db, artifact_id)


def list_reconcilable(db: Session, grace_period_seconds: int) -> list[Artifact]:
    """ADR 013's precise abandonment rule: never-claimed rows past the grace
    period, OR claimed rows whose lease has lapsed. A row with a still-live
    lease is never included here."""
    now = datetime.now(timezone.utc)
    grace_cutoff = now - timedelta(seconds=grace_period_seconds)
    return (
        db.query(Artifact)
        .filter(
            Artifact.status == "PENDING",
            (
                (Artifact.upload_lease_expires_at.is_(None) & (Artifact.created_at < grace_cutoff))
                | (Artifact.upload_lease_expires_at.isnot(None) & (Artifact.upload_lease_expires_at < now))
            ),
        )
        .all()
    )


def reconcile_to_uploaded(db: Session, artifact_id: uuid.UUID, size_bytes: int) -> Artifact | None:
    """Reconciler self-heal path -- same conditional shape, no uploader_id
    check (the original uploader is presumed gone; the Reconciler is acting
    on its behalf based on verified object-storage evidence)."""
    stmt = (
        sa_update(Artifact)
        .where(Artifact.id == artifact_id, Artifact.status == "PENDING")
        .values(status="UPLOADED", size_bytes=size_bytes, uploaded_at=datetime.now(timezone.utc), upload_lease_expires_at=None)
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount == 0:
        return None
    return get(db, artifact_id)


def reconcile_to_failed(db: Session, artifact_id: uuid.UUID) -> Artifact | None:
    stmt = (
        sa_update(Artifact)
        .where(Artifact.id == artifact_id, Artifact.status == "PENDING")
        .values(status="FAILED", upload_lease_expires_at=None)
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount == 0:
        return None
    return get(db, artifact_id)


def list_(db: Session, status: str | None, limit: int, offset: int) -> tuple[list[Artifact], int]:
    base = db.query(Artifact)
    if status is not None:
        base = base.filter(Artifact.status == status)
    total = base.count()
    items = base.order_by(Artifact.created_at.desc()).limit(limit).offset(offset).all()
    return items, total


def list_all_storage_keys_referenced(db: Session) -> set[str]:
    """For the Reconciler's orphan-detection sweep -- keys with a live
    (non-FAILED) reference."""
    rows = db.query(Artifact.storage_key).filter(Artifact.status != "FAILED").all()
    return {row[0] for row in rows}
