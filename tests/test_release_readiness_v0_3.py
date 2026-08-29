"""Closes the specific evidence gaps flagged in the V0.3 release-readiness
review: stale FAILED/retry writes (not just stale SUCCESS), recovery racing
cancellation, unknown-classification bounded retry, and recovery-process
crash mid-cycle only affecting the job it was processing."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.job import JobStatus
from app.repository import attempts as attempts_repo
from app.repository import jobs as repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services import worker as worker_module
from app.services.recovery import reclaim_stale_leases
from app.services.worker import process_job_message
from tests.conftest import reserve_for_claim


def _expire_lease(db_session, job_id):
    db_session.execute(
        text("UPDATE jobs SET lease_expires_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "id": str(job_id)},
    )
    db_session.commit()


def test_split_brain_stale_failed_write_rejected(db_session):
    """Stale worker's FAILED write (not just SUCCESS) must be fenced too."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    repo.claim(db_session, job.id, "worker-A", lease_duration_seconds=30)
    _expire_lease(db_session, job.id)
    reclaim_stale_leases(db_session)  # fences worker-A (status no longer RUNNING)

    stale_write = repo.finalize_attempt(db_session, job.id, "worker-A", 1, JobStatus.FAILED.value)

    assert stale_write is None
    final = repo.get(db_session, job.id)
    assert final.status != JobStatus.FAILED.value  # untouched by worker-A


def test_split_brain_stale_retry_requeue_write_rejected(db_session):
    """Stale worker's retry/requeue write (RUNNING -> QUEUED) must be fenced
    too -- a fenced-out worker must not be able to re-queue a job either."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    repo.claim(db_session, job.id, "worker-A", lease_duration_seconds=30)
    _expire_lease(db_session, job.id)
    reclaim_stale_leases(db_session)

    stale_requeue = repo.finalize_attempt(
        db_session, job.id, "worker-A", 1, JobStatus.QUEUED.value,
        extra_values={"next_retry_at": datetime.now(timezone.utc)},
    )

    assert stale_requeue is None


def test_recovery_does_not_resurrect_a_cancelled_job(db_session):
    """THE flagged race: worker A dies (lease will expire), user cancels the
    job while it's RUNNING, Recovery reclaims. Must land on CANCELLED, never
    QUEUED -- a cancelled-but-orphaned job must not re-enter the retry cycle."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    repo.claim(db_session, job.id, "worker-A", lease_duration_seconds=30)

    service.cancel_job(db_session, job.id)  # sets cancel_requested=true while RUNNING
    current = repo.get(db_session, job.id)
    assert current.cancel_requested is True
    assert current.status == JobStatus.RUNNING.value  # cooperative, not immediate

    _expire_lease(db_session, job.id)
    reclaimed_count = reclaim_stale_leases(db_session)

    assert reclaimed_count == 1
    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.CANCELLED.value  # NOT QUEUED
    assert final.next_retry_at is None  # no retry ever scheduled

    # And a claim attempt against it must fail -- it's terminal now.
    claim_attempt = repo.claim(db_session, job.id, "worker-B", lease_duration_seconds=30)
    assert claim_attempt is None


def test_unknown_classification_is_bounded_not_infinite_retry(db_session, monkeypatch):
    """An executor that fails without classifying (returns None) must still
    be bounded by MAX_ATTEMPTS, not retry forever."""
    from app.config import settings

    monkeypatch.setattr(settings, "max_attempts", 2)

    def fake_unknown_failure(job):
        return JobStatus.FAILED.value, "mystery failure", None  # unclassified

    monkeypatch.setattr(worker_module, "_run_executor", fake_unknown_failure)
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)

    process_job_message(db_session, job.id, worker_id="w1")  # attempt 1 -> retry (unknown, bounded)
    after_1 = repo.get(db_session, job.id)
    assert after_1.status == JobStatus.QUEUED.value
    attempt_1 = attempts_repo.get(db_session, job.id, 1)
    assert attempt_1.error_classification == "unknown"

    db_session.execute(text("UPDATE jobs SET next_retry_at = now() WHERE id = :id"), {"id": str(job.id)})
    db_session.commit()
    reserve_for_claim(db_session, repo.get(db_session, job.id))
    process_job_message(db_session, job.id, worker_id="w2")  # attempt 2 -> exhausted -> DLQ, not another retry

    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.FAILED.value
    assert final.attempt_number == 2
    from app.repository import dlq as dlq_repo

    entries, _ = dlq_repo.list_(db_session, limit=10, offset=0)
    assert any(e.job_id == job.id for e in entries)


def test_recovery_crash_mid_cycle_leaves_other_stale_job_untouched_and_reclaimable(db_session):
    """Simulates the Recovery process crashing after reclaiming job X but
    before reaching job Y in the same poll cycle (both stale). Job Y must be
    exactly as reclaimable afterward as if the crash never happened -- no
    partial state, no corruption, per FAILURE_SCENARIOS_V0.3.md #15."""
    job_x = service.create_job(db_session, JobCreate(job_type="sft"))
    job_y = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job_x)
    reserve_for_claim(db_session, job_y)
    repo.claim(db_session, job_x.id, "w1", lease_duration_seconds=30)
    repo.claim(db_session, job_y.id, "w2", lease_duration_seconds=30)
    _expire_lease(db_session, job_x.id)
    _expire_lease(db_session, job_y.id)

    # Simulate "the recovery process crashed after handling job X" by only
    # reclaiming X directly (bypassing the loop in reclaim_stale_leases that
    # would have continued on to Y).
    next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    result_x = repo.reclaim_stale(db_session, job_x.id, max_attempts=3, computed_next_retry_at=next_retry_at)
    assert result_x is not None
    attempts_repo.mark_lost(db_session, job_x.id, result_x[0], worker_id="w1")
    # "crash" here -- job_y never processed in this pass.

    y_before_restart = repo.get(db_session, job_y.id)
    assert y_before_restart.status == JobStatus.RUNNING.value  # untouched, still stale

    # "Recovery restarts" -- a fresh pass picks up whatever is still stale.
    reclaimed_count = reclaim_stale_leases(db_session)

    assert reclaimed_count == 1  # only job_y was still pending
    y_after = repo.get(db_session, job_y.id)
    assert y_after.status == JobStatus.QUEUED.value
    x_after = repo.get(db_session, job_x.id)
    assert x_after.status == JobStatus.QUEUED.value  # unaffected by the "crash", already handled
