import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.job import JobStatus
from app.repository import attempts as attempts_repo
from app.repository import jobs as repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services.worker import SIMULATED_FAILURE_JOB_TYPE, process_job_message
from tests.conftest import engine


def test_worker_executes_job_to_succeeded(db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))

    outcome = process_job_message(db_session, job.id, worker_id="w1")

    assert outcome == "claimed"
    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.SUCCEEDED.value
    assert final.attempt_number == 1
    attempt = attempts_repo.get(db_session, job.id, attempt_number=1)
    assert attempt.status == JobStatus.SUCCEEDED.value
    assert attempt.worker_id == "w1"


def test_worker_retries_transient_failure_then_succeeds(db_session):
    """Transient failure returns the job to QUEUED with next_retry_at set --
    ADR 005. Second attempt (against a job that no longer simulates failure)
    succeeds."""
    job = service.create_job(db_session, JobCreate(job_type=SIMULATED_FAILURE_JOB_TYPE))

    process_job_message(db_session, job.id, worker_id="w1")

    after_first = repo.get(db_session, job.id)
    assert after_first.status == JobStatus.QUEUED.value
    assert after_first.next_retry_at is not None
    assert after_first.attempt_number == 1
    first_attempt = attempts_repo.get(db_session, job.id, attempt_number=1)
    assert first_attempt.status == JobStatus.FAILED.value
    assert first_attempt.error_classification == "transient"

    # Force the retry to be due now, then flip job_type so attempt 2 succeeds
    # (simulating "the transient cause has cleared").
    db_session.execute(
        text("UPDATE jobs SET next_retry_at = now(), job_type = 'sft' WHERE id = :id"),
        {"id": str(job.id)},
    )
    db_session.commit()

    outcome = process_job_message(db_session, job.id, worker_id="w2")

    assert outcome == "claimed"
    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.SUCCEEDED.value
    assert final.attempt_number == 2


def test_permanent_failure_goes_straight_to_dlq(db_session):
    from app.repository import dlq as dlq_repo
    from app.services.worker import SIMULATE_PERMANENT_FAILURE_JOB_TYPE

    job = service.create_job(db_session, JobCreate(job_type=SIMULATE_PERMANENT_FAILURE_JOB_TYPE))

    process_job_message(db_session, job.id, worker_id="w1")

    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.FAILED.value
    assert final.attempt_number == 1  # no retry attempted
    entries, total = dlq_repo.list_(db_session, limit=10, offset=0)
    matching = [e for e in entries if e.job_id == job.id]
    assert len(matching) == 1
    assert matching[0].last_error_classification == "permanent"


def test_max_attempts_exhausted_goes_to_dlq(db_session, monkeypatch):
    from app.config import settings
    from app.repository import dlq as dlq_repo

    monkeypatch.setattr(settings, "max_attempts", 2)
    job = service.create_job(db_session, JobCreate(job_type=SIMULATED_FAILURE_JOB_TYPE))

    process_job_message(db_session, job.id, worker_id="w1")  # attempt 1 -> retry
    db_session.execute(
        text("UPDATE jobs SET next_retry_at = now() WHERE id = :id"), {"id": str(job.id)}
    )
    db_session.commit()
    process_job_message(db_session, job.id, worker_id="w2")  # attempt 2 -> exhausted

    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.FAILED.value
    assert final.attempt_number == 2
    entries, _ = dlq_repo.list_(db_session, limit=10, offset=0)
    matching = [e for e in entries if e.job_id == job.id]
    assert len(matching) == 1
    assert matching[0].total_attempts == 2


def test_duplicate_delivery_after_completion_is_a_no_op(db_session):
    """Failure scenario 3 (V0.2, re-verified under the attempt model): a
    message for an already-SUCCEEDED job must not re-execute or create a
    second attempt row."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    process_job_message(db_session, job.id, worker_id="w1")
    first_attempt = attempts_repo.get(db_session, job.id, attempt_number=1)

    outcome = process_job_message(db_session, job.id, worker_id="w2")

    assert outcome == "not_claimed"
    second_attempt = attempts_repo.get(db_session, job.id, attempt_number=1)
    assert second_attempt.started_at == first_attempt.started_at
    assert second_attempt.worker_id == "w1"  # untouched by the duplicate call


def test_concurrent_workers_only_one_claims_the_job():
    """Failure scenario 4 (V0.2): two workers receive the same job. Each uses
    its own DB session/connection, mirroring two real worker processes racing
    on the same message."""
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    setup_session = TestSession()
    job = service.create_job(setup_session, JobCreate(job_type="sft"))
    job_id = job.id
    setup_session.close()

    results = []

    def worker_attempt(worker_id):
        session = TestSession()
        try:
            results.append(process_job_message(session, job_id, worker_id))
        finally:
            session.close()

    threads = [threading.Thread(target=worker_attempt, args=(f"w{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("claimed") == 1
    assert results.count("not_claimed") == 4

    verify_session = TestSession()
    attempt_count = verify_session.execute(
        text("SELECT COUNT(*) FROM attempts WHERE job_id = :id"), {"id": str(job_id)}
    ).scalar()
    verify_session.close()
    assert attempt_count == 1


def test_worker_crash_after_claim_leaves_job_running_until_reclaimed(db_session):
    """V0.2's accepted gap, restated for V0.3: a job stuck RUNNING is not
    magically recovered by anything OTHER than the Recovery process (ADR 004).
    Reclamation itself is tested separately in test_recovery.py."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))

    claimed = repo.claim(db_session, job.id, worker_id="w-crashed", lease_duration_seconds=30)
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING.value
    # "worker crashes" here -- no heartbeat, no finalize, nothing further.

    # A duplicate/redelivered message for the same job must NOT auto-recover
    # it: claim requires QUEUED, job is RUNNING with a still-valid lease.
    outcome = process_job_message(db_session, job.id, worker_id="w-recovery-attempt")
    assert outcome == "not_claimed"

    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.RUNNING.value  # stuck until Recovery reclaims it
