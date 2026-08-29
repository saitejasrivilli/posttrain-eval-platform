from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.job import JobStatus
from app.repository import capacity as capacity_repo
from app.repository import jobs as repo
from app.repository import reservations as reservations_repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services.recovery import reclaim_stale_leases
from app.services.scheduler import try_admit
from app.services.worker import process_job_message


def _job_with_gpu(db, gpu):
    return service.create_job(db, JobCreate(job_type="sft", config={"resources": {"gpu": gpu}}))


def test_claim_requires_a_valid_reservation_hard_invariant(db_session):
    """The hard invariant: a worker can never claim QUEUED+no-reservation.
    V0.4's scheduler cannot be bypassed."""
    job = _job_with_gpu(db_session, gpu=1)
    # Deliberately do NOT call try_admit -- no reservation exists.

    claimed = repo.claim(db_session, job.id, "w1", lease_duration_seconds=30)

    assert claimed is None
    outcome = process_job_message(db_session, job.id, worker_id="w1")
    assert outcome == "not_claimed"


def test_worker_success_releases_reservation(db_session):
    job = _job_with_gpu(db_session, gpu=2)
    try_admit(db_session, job)
    assert capacity_repo.get(db_session).allocated_gpu == 2

    outcome = process_job_message(db_session, job.id, worker_id="w1")

    assert outcome == "claimed"
    assert repo.get(db_session, job.id).status == JobStatus.SUCCEEDED.value
    assert capacity_repo.get(db_session).allocated_gpu == 0
    reservation = reservations_repo.get(db_session, job.id, attempt_number=1)
    assert reservation.status == "RELEASED"


def test_worker_retry_releases_old_reservation_new_attempt_needs_new_one(db_session):
    from app.services.worker import SIMULATED_FAILURE_JOB_TYPE

    job = service.create_job(
        db_session, JobCreate(job_type=SIMULATED_FAILURE_JOB_TYPE, config={"resources": {"gpu": 1}})
    )
    try_admit(db_session, job)

    process_job_message(db_session, job.id, worker_id="w1")  # fails transiently -> QUEUED for retry

    assert capacity_repo.get(db_session).allocated_gpu == 0  # attempt 1's reservation released
    after = repo.get(db_session, job.id)
    assert after.status == JobStatus.QUEUED.value

    # A claim attempt now must fail: attempt 1's reservation is released, and
    # no reservation exists yet for attempt 2 -- the Scheduler must admit it again.
    claim_attempt = repo.claim(db_session, job.id, "w2", lease_duration_seconds=30)
    assert claim_attempt is None


def test_recovery_releases_reservation_atomically_with_marking_lost(db_session):
    """The most important V0.3<->V0.4 integration point (per project review):
    reservation release must happen in the SAME transaction as marking the
    attempt LOST -- never leaked, never released-without-LOST either."""
    job = _job_with_gpu(db_session, gpu=3)
    try_admit(db_session, job)
    assert capacity_repo.get(db_session).allocated_gpu == 3
    claimed = repo.claim(db_session, job.id, "w1", lease_duration_seconds=30)
    assert claimed is not None

    db_session.execute(
        text("UPDATE jobs SET lease_expires_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "id": str(job.id)},
    )
    db_session.commit()

    reclaimed = reclaim_stale_leases(db_session)

    assert reclaimed == 1
    assert capacity_repo.get(db_session).allocated_gpu == 0  # released
    reservation = reservations_repo.get(db_session, job.id, attempt_number=1)
    assert reservation.status == "RELEASED"
    from app.repository import attempts as attempts_repo

    attempt = attempts_repo.get(db_session, job.id, attempt_number=1)
    assert attempt.status == "LOST"


def test_cancel_releases_reservation_for_unclaimed_job(db_session):
    """Scenario 4 (FAILURE_SCENARIOS_V0.4.md): cancelling a QUEUED-with-
    reservation job (admitted but never claimed) must release its reservation."""
    job = _job_with_gpu(db_session, gpu=1)
    try_admit(db_session, job)
    assert capacity_repo.get(db_session).allocated_gpu == 1

    result = service.cancel_job(db_session, job.id)

    assert result.status == JobStatus.CANCELLED.value
    assert capacity_repo.get(db_session).allocated_gpu == 0
    reservation = reservations_repo.get(db_session, job.id, attempt_number=1)
    assert reservation.status == "RELEASED"


def test_scheduler_never_admits_cancelled_job(db_session):
    job = _job_with_gpu(db_session, gpu=1)
    service.cancel_job(db_session, job.id)  # QUEUED -> CANCELLED

    admitted = try_admit(db_session, job)

    assert admitted is False
    assert reservations_repo.get(db_session, job.id, attempt_number=1) is None


def test_scheduler_never_admits_retry_not_yet_due(db_session):
    job = _job_with_gpu(db_session, gpu=1)
    db_session.execute(
        text("UPDATE jobs SET next_retry_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) + timedelta(seconds=30), "id": str(job.id)},
    )
    db_session.commit()
    fresh = repo.get(db_session, job.id)

    admitted = try_admit(db_session, fresh)

    assert admitted is False
    assert reservations_repo.get(db_session, job.id, attempt_number=1) is None
