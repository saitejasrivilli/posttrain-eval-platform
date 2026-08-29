import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.repository import artifacts as artifacts_repo
from app.storage import object_exists_with_hash

logger = logging.getLogger("app")


def reconcile_pending_artifacts(db: Session, storage_client) -> tuple[int, int]:
    """ADR 013: for each PENDING artifact past the abandonment threshold
    (never-claimed + grace period elapsed, OR claimed but lease lapsed --
    never a row with a still-live lease), check object storage. Present +
    hash-verified -> self-heal to UPLOADED. Absent -> FAILED."""
    healed = 0
    failed = 0
    for artifact in artifacts_repo.list_reconcilable(db, settings.artifact_pending_grace_period_seconds):
        if object_exists_with_hash(storage_client, artifact.storage_key, artifact.content_hash):
            # Size isn't separately tracked here; a real implementation would
            # HEAD for content-length. Using 0 as "unknown, but bytes verified
            # present and hash-correct" would understate size -- so re-fetch.
            size = _object_size(storage_client, artifact.storage_key)
            result = artifacts_repo.reconcile_to_uploaded(db, artifact.id, size)
            if result is not None:
                healed += 1
                logger.info("artifact_reconciled_uploaded", extra={"artifact_id": str(artifact.id)})
        else:
            result = artifacts_repo.reconcile_to_failed(db, artifact.id)
            if result is not None:
                failed += 1
                logger.info("artifact_reconciled_failed", extra={"artifact_id": str(artifact.id)})
    return healed, failed


def _object_size(storage_client, storage_key: str) -> int:
    from app.config import settings as app_settings

    obj = storage_client.get_object(Bucket=app_settings.minio_bucket, Key=storage_key)
    return obj["ContentLength"]


def detect_orphans(db: Session, storage_client) -> list[str]:
    """Lower-frequency sweep (ARTIFACT_LIFECYCLE_V0.5.md): keys in storage
    with no live metadata reference. Logged only -- never auto-deleted
    (ADR 013)."""
    referenced = artifacts_repo.list_all_storage_keys_referenced(db)
    orphans = []
    paginator = storage_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.minio_bucket):
        for obj in page.get("Contents", []):
            if obj["Key"] not in referenced:
                orphans.append(obj["Key"])
    if orphans:
        logger.info("orphan_artifacts_detected", extra={"keys": orphans})
    return orphans


def run_once(db: Session, storage_client) -> tuple[int, int]:
    return reconcile_pending_artifacts(db, storage_client)
