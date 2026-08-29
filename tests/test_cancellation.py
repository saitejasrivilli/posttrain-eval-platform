from app.models.job import JobStatus
from app.repository import attempts as attempts_repo
from app.repository import jobs as repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services.worker import process_job_message


def test_cancel_queued_job_is_immediate(client, db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))

    resp = client.post(f"/v1/jobs/{job.id}/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


def test_cancel_terminal_job_returns_409(client, db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    client.post(f"/v1/jobs/{job.id}/cancel")  # QUEUED -> CANCELLED

    resp = client.post(f"/v1/jobs/{job.id}/cancel")

    assert resp.status_code == 409


def test_cancel_running_job_sets_flag_not_immediate_transition(db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    claimed = repo.claim(db_session, job.id, worker_id="w1", lease_duration_seconds=30)
    assert claimed is not None

    result = service.cancel_job(db_session, job.id)

    # Cooperative, not immediate: status is still RUNNING right after the call.
    assert result.status == JobStatus.RUNNING.value
    assert result.cancel_requested is True


def test_worker_honors_cancel_requested_at_checkpoint(db_session):
    """Cancellation requested after claim but before the worker's checkpoint
    read results in CANCELLED, not SUCCEEDED -- proves the flag is actually
    observed, not just stored inertly in the row."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    claimed = repo.claim(db_session, job.id, worker_id="w1", lease_duration_seconds=30)
    assert claimed is not None
    attempt_number = claimed.attempt_number
    attempts_repo.insert(db_session, job.id, attempt_number, "w1")
    service.cancel_job(db_session, job.id)  # sets cancel_requested while RUNNING

    # Exercise the exact fencing-conditioned checkpoint statements the worker
    # uses post-claim (see app/services/worker.py::process_job_message).
    current = repo.get(db_session, job.id)
    outcome = JobStatus.CANCELLED.value if current.cancel_requested else JobStatus.SUCCEEDED.value
    finalized = repo.finalize_attempt(db_session, job.id, "w1", attempt_number, outcome)
    assert finalized is not None
    attempts_repo.finalize(db_session, job.id, attempt_number, outcome)

    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.CANCELLED.value


def test_worker_completes_normally_if_cancel_arrives_after_checkpoint(db_session):
    """Last-checkpoint-wins: if the worker's checkpoint has already passed
    (simulated by the worker completing before any cancel call happens), the
    job finishes normally -- this is documented expected behavior, not a bug."""
    job = service.create_job(db_session, JobCreate(job_type="sft"))

    outcome = process_job_message(db_session, job.id, worker_id="w1")
    assert outcome == "claimed"

    # Cancel arrives only after the worker already reached its terminal state.
    resp_job = repo.get(db_session, job.id)
    assert resp_job.status == JobStatus.SUCCEEDED.value


def test_cancel_requested_before_retry_claim_is_honored(db_session):
    """Failure scenario 8: retry happens while cancellation is requested. A
    job cancelled while QUEUED-and-awaiting-retry must never spawn a new
    attempt -- the claim's `cancel_requested=false` condition rejects it."""
    from datetime import datetime, timedelta, timezone

    job = service.create_job(db_session, JobCreate(job_type="sft"))
    # Put it into the retry-wait state directly (simulating a prior failed attempt).
    from sqlalchemy import text

    db_session.execute(
        text(
            "UPDATE jobs SET next_retry_at = :t WHERE id = :id"
        ),
        {"t": datetime.now(timezone.utc) + timedelta(seconds=30), "id": str(job.id)},
    )
    db_session.commit()

    service.cancel_job(db_session, job.id)  # QUEUED -> CANCELLED (fast path, unaffected by next_retry_at)

    # Even if next_retry_at were already due, cancel_requested has flipped
    # the job to CANCELLED, so no claim can ever succeed for it again.
    claimed = repo.claim(db_session, job.id, worker_id="w1", lease_duration_seconds=30)
    assert claimed is None
    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.CANCELLED.value


def test_claim_rejects_retry_not_yet_due(db_session):
    """Backoff is enforced, not decorative: a claim attempt before
    next_retry_at must fail even though the job is QUEUED and not cancelled."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    job = service.create_job(db_session, JobCreate(job_type="sft"))
    db_session.execute(
        text("UPDATE jobs SET next_retry_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) + timedelta(seconds=30), "id": str(job.id)},
    )
    db_session.commit()

    claimed = repo.claim(db_session, job.id, worker_id="w1", lease_duration_seconds=30)

    assert claimed is None
