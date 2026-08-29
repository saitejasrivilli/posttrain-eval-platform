import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.job import JobStatus
from app.repository import executions as executions_repo
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
    execution = executions_repo.get(db_session, job.id)
    assert execution.outcome == JobStatus.SUCCEEDED.value
    assert execution.worker_id == "w1"


def test_worker_marks_job_failed_for_simulated_failure(db_session):
    job = service.create_job(db_session, JobCreate(job_type=SIMULATED_FAILURE_JOB_TYPE))

    process_job_message(db_session, job.id, worker_id="w1")

    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.FAILED.value


def test_duplicate_delivery_after_completion_is_a_no_op(db_session):
    """Failure scenario 3: duplicate Kafka delivery. Message for an
    already-SUCCEEDED job must not re-execute or create a second execution row."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    process_job_message(db_session, job.id, worker_id="w1")
    first_execution = executions_repo.get(db_session, job.id)

    outcome = process_job_message(db_session, job.id, worker_id="w2")

    assert outcome == "not_claimed"
    second_execution = executions_repo.get(db_session, job.id)
    assert second_execution.started_at == first_execution.started_at
    assert second_execution.worker_id == "w1"  # untouched by the duplicate call


def test_concurrent_workers_only_one_claims_the_job():
    """Failure scenario 4: two workers receive the same job. Each worker uses
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
    execution_count = (
        verify_session.execute(
            __import__("sqlalchemy").text("SELECT COUNT(*) FROM executions WHERE job_id = :id"),
            {"id": str(job_id)},
        ).scalar()
    )
    verify_session.close()
    assert execution_count == 1


def test_worker_crash_after_claim_leaves_job_running(db_session):
    """Failure scenario 5: worker crashes after acquiring a job. Simulates the
    crash by claiming (QUEUED->RUNNING) and stopping -- never calling the rest
    of process_job_message. Invariant: nothing in V0.2 may later transition
    this job out of RUNNING on its own."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))

    claimed = repo.conditional_transition(
        db_session,
        job.id,
        valid_from=[JobStatus.QUEUED.value],
        to_status=JobStatus.RUNNING.value,
    )
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING.value
    # "worker crashes" here -- no further action taken.

    # A second worker's attempt to process a redelivered/duplicate message for
    # the same job must NOT auto-recover it: claim requires QUEUED, job is RUNNING.
    outcome = process_job_message(db_session, job.id, worker_id="w-recovery-attempt")
    assert outcome == "not_claimed"

    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.RUNNING.value  # stuck, as documented -- not SUCCEEDED
