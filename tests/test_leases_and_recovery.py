import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.models.job import JobStatus
from app.repository import attempts as attempts_repo
from app.repository import jobs as repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services.recovery import reclaim_stale_leases
from tests.conftest import engine, reserve_for_claim


def _expire_lease(db_session, job_id):
    db_session.execute(
        text("UPDATE jobs SET lease_expires_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "id": str(job_id)},
    )
    db_session.commit()


def test_heartbeat_renews_lease(db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, job.id, "w1", lease_duration_seconds=30)
    original_expiry = claimed.lease_expires_at

    renewed = repo.heartbeat(db_session, job.id, "w1", claimed.attempt_number, lease_duration_seconds=30)

    assert renewed is True
    after = repo.get(db_session, job.id)
    assert after.lease_expires_at > original_expiry


def test_heartbeat_fails_after_fencing_out(db_session):
    """A worker whose attempt_number has been superseded cannot renew."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, job.id, "w1", lease_duration_seconds=30)
    _expire_lease(db_session, job.id)
    reclaim_stale_leases(db_session)  # worker-B (Recovery) takes over -> attempt_number+1

    renewed = repo.heartbeat(db_session, job.id, "w1", claimed.attempt_number, lease_duration_seconds=30)

    assert renewed is False


def test_stale_lease_is_reclaimed_and_old_attempt_marked_lost(db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, job.id, "w1", lease_duration_seconds=30)
    claimed_attempt_number = claimed.attempt_number  # capture before any further commit expires it
    attempts_repo.insert(db_session, job.id, claimed_attempt_number, "w1")
    _expire_lease(db_session, job.id)

    reclaimed_count = reclaim_stale_leases(db_session)

    assert reclaimed_count == 1
    after = repo.get(db_session, job.id)
    assert after.attempt_number == claimed_attempt_number  # reclaim doesn't advance the token
    assert after.status == JobStatus.QUEUED.value
    assert after.next_retry_at is not None
    old_attempt = attempts_repo.get(db_session, job.id, claimed_attempt_number)
    assert old_attempt.status == "LOST"
    assert old_attempt.error_classification == "transient"


def test_split_brain_original_worker_cannot_commit_after_reclamation(db_session):
    """THE critical test (release-blocking per project directive). Worker A
    claims attempt 1, its lease expires, Recovery reclaims (attempt 2,
    Worker B's territory), Worker B completes the job. Worker A then tries to
    report SUCCESS using its stale attempt_number -- this must affect zero
    rows and must not alter the already-finalized job."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)

    # Worker A claims attempt 1.
    claimed_a = repo.claim(db_session, job.id, "worker-A", lease_duration_seconds=30)
    assert claimed_a.attempt_number == 1
    attempts_repo.insert(db_session, job.id, 1, "worker-A")

    # Worker A's lease expires (it's partitioned/paused); Recovery reclaims.
    _expire_lease(db_session, job.id)
    reclaimed_count = reclaim_stale_leases(db_session)
    assert reclaimed_count == 1
    after_reclaim = repo.get(db_session, job.id)
    # Reclaim does NOT advance attempt_number -- it fences worker-A purely by
    # moving status away from RUNNING (ADR 004). claim() below is what
    # advances the token for the real next attempt.
    assert after_reclaim.attempt_number == 1
    assert after_reclaim.status == JobStatus.QUEUED.value
    # Force the retry backoff to be due immediately -- this test is about
    # fencing, not backoff timing (that's covered separately).
    db_session.execute(
        text("UPDATE jobs SET next_retry_at = now() WHERE id = :id"), {"id": str(job.id)}
    )
    db_session.commit()
    reserve_for_claim(db_session, repo.get(db_session, job.id))

    # Worker B claims attempt 2 and completes it successfully.
    claimed_b = repo.claim(db_session, job.id, "worker-B", lease_duration_seconds=30)
    assert claimed_b.attempt_number == 2
    attempts_repo.insert(db_session, job.id, 2, "worker-B")
    finalized_b = repo.finalize_attempt(db_session, job.id, "worker-B", 2, JobStatus.SUCCEEDED.value)
    assert finalized_b is not None
    attempts_repo.finalize(db_session, job.id, 2, JobStatus.SUCCEEDED.value)

    job_after_b = repo.get(db_session, job.id)
    assert job_after_b.status == JobStatus.SUCCEEDED.value

    # Worker A, unaware it was fenced out, "resumes" and reports success using
    # its stale attempt_number=1.
    stale_write = repo.finalize_attempt(db_session, job.id, "worker-A", 1, JobStatus.SUCCEEDED.value)

    assert stale_write is None  # rejected: 0 rows affected
    job_final = repo.get(db_session, job.id)
    assert job_final.status == JobStatus.SUCCEEDED.value  # untouched, still worker-B's result
    assert job_final.attempt_number == 2  # not corrupted back to 1
    # Attempt 2's record is the authoritative one, unaffected by A's late write.
    attempt_2 = attempts_repo.get(db_session, job.id, 2)
    assert attempt_2.worker_id == "worker-B"
    assert attempt_2.status == JobStatus.SUCCEEDED.value


def test_split_brain_original_worker_heartbeat_also_rejected(db_session):
    """Same split-brain scenario, but Worker A's stale write is a heartbeat
    renewal instead of a terminal commit -- also must be rejected."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    claimed_a = repo.claim(db_session, job.id, "worker-A", lease_duration_seconds=30)
    _expire_lease(db_session, job.id)
    reclaim_stale_leases(db_session)

    renewed = repo.heartbeat(db_session, job.id, "worker-A", claimed_a.attempt_number, lease_duration_seconds=30)

    assert renewed is False


def test_two_recovery_processes_race_the_same_stale_job(db_session):
    """Only one of several concurrent reclaim attempts may succeed."""
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    setup = TestSession()
    job = service.create_job(setup, JobCreate(job_type="sft"))
    job_id = job.id  # capture before the session that created it is closed
    reserve_for_claim(setup, job)
    repo.claim(setup, job_id, "w1", lease_duration_seconds=30)
    _expire_lease(setup, job_id)
    setup.close()

    results = []

    def attempt_reclaim():
        session = TestSession()
        try:
            results.append(reclaim_stale_leases(session))
        finally:
            session.close()

    threads = [threading.Thread(target=attempt_reclaim) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1  # exactly one process reclaimed it

    verify = TestSession()
    final = repo.get(verify, job_id)
    # sum(results) == 1 above already proves exactly one thread's atomic
    # reclaim UPDATE affected a row; confirm the resulting state is coherent
    # (not left half-modified by a "losing" thread that somehow also wrote).
    assert final.status == JobStatus.QUEUED.value
    assert final.lease_owner is None
    assert final.attempt_number == 1  # reclaim doesn't advance the token (ADR 004)
    verify.close()


def test_slow_but_healthy_worker_is_never_reclaimed(db_session):
    """A worker whose heartbeats keep arriving must never be reclaimed, no
    matter how long its execution body runs."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, job.id, "w1", lease_duration_seconds=2)

    # Simulate 3 heartbeats over a span longer than the lease duration would
    # allow without renewal.
    for _ in range(3):
        time.sleep(1)
        renewed = repo.heartbeat(db_session, job.id, "w1", claimed.attempt_number, lease_duration_seconds=2)
        assert renewed is True
        reclaimed_count = reclaim_stale_leases(db_session)
        assert reclaimed_count == 0  # never reclaimed -- lease kept current

    final = repo.get(db_session, job.id)
    assert final.attempt_number == 1
    assert final.status == JobStatus.RUNNING.value


def test_retry_backoff_next_retry_at_moves_forward_with_attempt_number():
    from app.retry_policy import compute_next_retry_at

    t1 = compute_next_retry_at(1, base_delay_seconds=2, max_delay_seconds=60, jitter_ratio=0)
    t2 = compute_next_retry_at(2, base_delay_seconds=2, max_delay_seconds=60, jitter_ratio=0)
    t3 = compute_next_retry_at(3, base_delay_seconds=2, max_delay_seconds=60, jitter_ratio=0)
    now = datetime.now(timezone.utc)

    # attempt 1 -> ~2s, attempt 2 -> ~4s, attempt 3 -> ~8s (no jitter, exact doubling)
    assert (t1 - now).total_seconds() < (t2 - now).total_seconds() < (t3 - now).total_seconds()


def test_retry_backoff_respects_max_delay_cap():
    from app.retry_policy import compute_next_retry_at

    t = compute_next_retry_at(10, base_delay_seconds=2, max_delay_seconds=5, jitter_ratio=0)
    now = datetime.now(timezone.utc)

    assert (t - now).total_seconds() <= 5.5  # capped at max_delay, no jitter here


def test_retry_backoff_jitter_adds_randomness_within_ratio():
    from app.retry_policy import compute_next_retry_at

    now = datetime.now(timezone.utc)
    samples = [
        (compute_next_retry_at(1, base_delay_seconds=10, max_delay_seconds=60, jitter_ratio=0.2) - now).total_seconds()
        for _ in range(20)
    ]

    assert min(samples) >= 10  # never less than the base capped delay
    assert max(samples) <= 12.5  # never more than base + jitter_ratio, with margin
    assert len(set(round(s, 3) for s in samples)) > 1  # actually randomized, not constant
