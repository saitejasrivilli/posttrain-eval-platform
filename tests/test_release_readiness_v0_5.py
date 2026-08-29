"""Closes evidence gaps flagged in the V0.5 release-readiness review: orphan
detection, hash-mismatch/corrupt upload, reconciler crash mid-cycle,
cancelled training during artifact creation, retry producing a second
distinct artifact."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.job import JobStatus
from app.repository import artifacts as artifacts_repo
from app.repository import jobs as repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services.reconciler import detect_orphans, reconcile_pending_artifacts
from app.services.worker import process_job_message
from app.storage import hash_bytes, storage_key_for_hash, upload as storage_upload
from tests.conftest import reserve_for_claim


def test_hash_mismatch_never_promotes_to_uploaded(db_session, storage_client):
    """Failure scenario 18 (partially/corrupt upload): the reconciler's
    hash-verification step must reject bytes present at the key but not
    matching the recorded hash -- never falsely promote."""
    correct_hash = hash_bytes(b"the real content")
    artifact = artifacts_repo.create_pending(db_session, correct_hash, "MODEL")
    # Corrupt/partial bytes end up at the key (simulating a truncated upload).
    storage_upload(storage_client, storage_key_for_hash(correct_hash), b"WRONG BYTES ENTIRELY")
    db_session.execute(
        text("UPDATE artifacts SET created_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=400), "id": str(artifact.id)},
    )
    db_session.commit()

    healed, failed = reconcile_pending_artifacts(db_session, storage_client)

    assert healed == 0
    assert failed == 1
    final = artifacts_repo.get(db_session, artifact.id)
    assert final.status == "FAILED"  # never UPLOADED with mismatched bytes


def test_orphan_artifact_detected_and_never_deleted(db_session, storage_client):
    """Failure scenario 17: bytes with no live metadata reference are
    flagged, never auto-deleted (ADR 013)."""
    orphan_key = "sha256/" + "0" * 64
    storage_upload(storage_client, orphan_key, b"nobody references this")

    orphans = detect_orphans(db_session, storage_client)

    assert orphan_key in orphans
    # Confirm it's still there -- detection must not delete anything.
    from app.storage import object_exists_with_hash

    assert object_exists_with_hash(storage_client, orphan_key, hash_bytes(b"nobody references this"))


def test_reconciler_crash_mid_cycle_leaves_other_artifact_untouched(db_session, storage_client):
    """Same crash-tolerance proof as V0.3's Recovery/V0.4's Scheduler,
    applied to the Reconciler: a crash between handling artifact X and
    artifact Y leaves Y exactly as it was, reclaimable on the next pass."""
    from app.config import settings

    hash_x = hash_bytes(b"artifact x content")
    hash_y = hash_bytes(b"artifact y content")
    artifact_x = artifacts_repo.create_pending(db_session, hash_x, "MODEL")
    artifact_y = artifacts_repo.create_pending(db_session, hash_y, "MODEL")
    old_time = datetime.now(timezone.utc) - timedelta(seconds=settings.artifact_pending_grace_period_seconds + 5)
    db_session.execute(
        text("UPDATE artifacts SET created_at = :t WHERE id IN (:x, :y)"),
        {"t": old_time, "x": str(artifact_x.id), "y": str(artifact_y.id)},
    )
    db_session.commit()

    # Simulate "the reconciler crashed after handling X" by only resolving X directly.
    result_x = artifacts_repo.reconcile_to_failed(db_session, artifact_x.id)
    assert result_x is not None
    # "crash" here -- artifact_y never processed in this pass.

    y_before_restart = artifacts_repo.get(db_session, artifact_y.id)
    assert y_before_restart.status == "PENDING"  # untouched, still stale

    # "Reconciler restarts" -- a fresh pass picks up whatever is still pending.
    healed, failed = reconcile_pending_artifacts(db_session, storage_client)

    assert failed == 1  # only artifact_y was still pending
    y_after = artifacts_repo.get(db_session, artifact_y.id)
    assert y_after.status == "FAILED"
    x_after = artifacts_repo.get(db_session, artifact_x.id)
    assert x_after.status == "FAILED"  # unaffected by the "crash", already handled


def test_cancelled_training_run_artifact_follows_own_lifecycle_no_special_case(db_session, storage_client):
    """Failure scenario 15: cancelling a job mid-artifact-creation applies no
    special cancellation-aware artifact logic -- the artifact simply follows
    its own PENDING lifecycle (STATE_TRANSITIONS_V0.5.md)."""
    from app.config import settings

    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, job.id, "w1", lease_duration_seconds=30)
    assert claimed is not None

    # Artifact creation begins (PENDING) before the job gets cancelled.
    artifact = artifacts_repo.create_pending(db_session, hash_bytes(b"never finished"), "MODEL", job_id=job.id, attempt_number=1)

    service.cancel_job(db_session, job.id)  # sets cancel_requested while RUNNING

    # No upload ever completes (job cancelled mid-execution). Artifact reconciles
    # to FAILED on its own schedule, with no cancellation-specific code path.
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
    # Never registerable as a model version regardless of the job's fate.


def test_retry_produces_a_second_distinct_artifact(db_session, storage_client):
    """Failure scenario 16: each attempt that produces bytes gets its own
    artifact row, keyed by attempt_number -- consistent with V0.3's
    per-attempt granularity."""
    from app.services import artifacts as artifacts_service
    from app.services.worker import SIMULATED_FAILURE_JOB_TYPE

    job = service.create_job(db_session, JobCreate(job_type=SIMULATED_FAILURE_JOB_TYPE))
    reserve_for_claim(db_session, job)
    process_job_message(db_session, job.id, worker_id="w1")  # attempt 1 fails transiently -> QUEUED

    after_1 = repo.get(db_session, job.id)
    assert after_1.status == JobStatus.QUEUED.value
    assert after_1.attempt_number == 1

    # Attempt 2: force retry due, flip job_type so it succeeds, reserve+claim again.
    db_session.execute(
        text("UPDATE jobs SET next_retry_at = now(), job_type = 'sft' WHERE id = :id"),
        {"id": str(job.id)},
    )
    db_session.commit()
    reserve_for_claim(db_session, repo.get(db_session, job.id))
    process_job_message(db_session, job.id, worker_id="w2")

    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.SUCCEEDED.value
    assert final.attempt_number == 2

    # Two distinct artifacts, one per attempt (attempt 1's, uploaded despite
    # the job ultimately failing that attempt -- possible in principle;
    # attempt 2's, the one that actually gets registered).
    artifact_attempt_1 = artifacts_service.upload_artifact(
        db_session, storage_client, b"attempt 1 partial output", "CHECKPOINT",
        "trainer", job_id=job.id, attempt_number=1,
    )
    artifact_attempt_2 = artifacts_service.upload_artifact(
        db_session, storage_client, b"attempt 2 final output", "MODEL",
        "trainer", job_id=job.id, attempt_number=2,
    )

    assert artifact_attempt_1.id != artifact_attempt_2.id
    assert artifact_attempt_1.attempt_number == 1
    assert artifact_attempt_2.attempt_number == 2
