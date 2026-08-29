"""Release-blocking tests (ADR 013), same tier as V0.3's split-brain test and
V0.4's no-overcommit test: the artifact metadata-first + upload-lease +
reconciliation model, verified against real logic (moto-mocked S3, real
Postgres)."""
import threading
import time
from datetime import datetime, timedelta, timezone

from app.repository import artifacts as artifacts_repo
from app.services import artifacts as artifacts_service
from app.services.reconciler import reconcile_pending_artifacts
from app.storage import hash_bytes, storage_key_for_hash, upload as storage_upload


def test_upload_happy_path_reaches_uploaded(db_session, storage_client):
    data = b"hello world dataset content"

    artifact = artifacts_service.upload_artifact(db_session, storage_client, data, "DATASET", "uploader-1")

    assert artifact.status == "UPLOADED"
    assert artifact.content_hash == hash_bytes(data)
    assert artifact.size_bytes == len(data)


def test_duplicate_upload_is_idempotent_no_duplicate_row(db_session, storage_client):
    data = b"identical content"

    first = artifacts_service.upload_artifact(db_session, storage_client, data, "MODEL", "uploader-1")
    second = artifacts_service.upload_artifact(db_session, storage_client, data, "MODEL", "uploader-2")

    assert first.id == second.id  # same row, no duplicate
    assert artifacts_repo.get_by_hash(db_session, hash_bytes(data)) is not None


def test_metadata_committed_upload_never_happens_reconciles_to_failed(db_session, storage_client):
    """Failure scenario 2: PENDING row, never claimed, grace period elapses."""
    from app.config import settings

    artifact = artifacts_repo.create_pending(db_session, "deadbeef" * 8, "MODEL")
    # Backdate created_at past the grace period, never claim a lease.
    from sqlalchemy import text

    db_session.execute(
        text("UPDATE artifacts SET created_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=settings.artifact_pending_grace_period_seconds + 5),
         "id": str(artifact.id)},
    )
    db_session.commit()

    healed, failed = reconcile_pending_artifacts(db_session, storage_client)

    assert failed == 1
    final = artifacts_repo.get(db_session, artifact.id)
    assert final.status == "FAILED"


def test_upload_succeeds_but_status_flip_lost_reconciler_self_heals(db_session, storage_client):
    """Failure scenario 1 (the self-heal path): bytes uploaded directly
    (simulating the flip-to-UPLOADED update failing/crashing after a real
    upload succeeded), row stays PENDING with an expired lease -> Reconciler
    finds the bytes, verifies hash, self-heals to UPLOADED WITHOUT re-uploading."""
    data = b"bytes that made it, but the flip was lost"
    content_hash = hash_bytes(data)
    artifact = artifacts_repo.create_pending(db_session, content_hash, "CHECKPOINT")
    storage_upload(storage_client, storage_key_for_hash(content_hash), data)  # bytes ARE present
    # Simulate an expired lease (uploader claimed, then "crashed" before flip).
    claimed = artifacts_repo.claim_upload_lease(db_session, artifact.id, "uploader-x", lease_duration_seconds=1)
    assert claimed is not None
    time.sleep(1.1)  # let the lease lapse

    healed, failed = reconcile_pending_artifacts(db_session, storage_client)

    assert healed == 1
    final = artifacts_repo.get(db_session, artifact.id)
    assert final.status == "UPLOADED"
    assert final.size_bytes == len(data)


def test_pending_row_with_live_lease_is_never_touched_by_reconciler(db_session, storage_client):
    """Race 1 (design review requirement): uploader actively holds a live
    lease -- Reconciler must not act on this row at all, no matter its age."""
    from sqlalchemy import text

    artifact = artifacts_repo.create_pending(db_session, "cafebabe" * 8, "MODEL")
    artifacts_repo.claim_upload_lease(db_session, artifact.id, "uploader-1", lease_duration_seconds=60)
    # Backdate created_at (irrelevant once claimed -- only the lease matters).
    db_session.execute(
        text("UPDATE artifacts SET created_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(hours=1), "id": str(artifact.id)},
    )
    db_session.commit()

    healed, failed = reconcile_pending_artifacts(db_session, storage_client)

    assert healed == 0
    assert failed == 0
    final = artifacts_repo.get(db_session, artifact.id)
    assert final.status == "PENDING"  # untouched


def test_uploader_renewing_lease_survives_concurrent_reconciler_sweeps(db_session, storage_client):
    """Race 1, live version: an uploader claims and repeatedly renews its
    lease while Reconciler sweeps run concurrently -- must never be marked
    FAILED while the lease is live, and must reach UPLOADED once complete."""
    from app.config import settings

    data = b"a slow but healthy upload"
    content_hash = hash_bytes(data)
    artifact = artifacts_repo.create_pending(db_session, content_hash, "MODEL")
    artifacts_repo.claim_upload_lease(db_session, artifact.id, "uploader-1", lease_duration_seconds=1)

    stop = threading.Event()
    results = []

    def reconciler_loop():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from tests.conftest import engine

        Session = sessionmaker(bind=engine)
        while not stop.is_set():
            session = Session()
            try:
                results.append(reconcile_pending_artifacts(session, storage_client))
            finally:
                session.close()
            time.sleep(0.2)

    t = threading.Thread(target=reconciler_loop)
    t.start()

    # Uploader renews faster than the lease would expire, "uploads" partway through.
    for _ in range(4):
        renewed = artifacts_repo.renew_upload_lease(db_session, artifact.id, "uploader-1", lease_duration_seconds=1)
        assert renewed
        time.sleep(0.3)

    storage_upload(storage_client, storage_key_for_hash(content_hash), data)
    finalized = artifacts_repo.mark_uploaded(db_session, artifact.id, "uploader-1", size_bytes=len(data))
    assert finalized is not None
    assert finalized.status == "UPLOADED"

    stop.set()
    t.join()

    total_failed = sum(f for _, f in results)
    assert total_failed == 0  # never marked FAILED while the lease was live
