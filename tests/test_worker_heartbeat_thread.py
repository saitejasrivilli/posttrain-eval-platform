from app.models.job import JobStatus
from app.repository import jobs as repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services.recovery import reclaim_stale_leases
from app.services.worker import process_job_message


def test_real_heartbeat_thread_keeps_slow_job_alive(db_session, monkeypatch):
    """Exercises the ACTUAL background heartbeat thread in
    app/services/worker.py -- not manual heartbeat() calls -- against a
    simulated execution body that sleeps longer than the lease duration would
    allow without renewal. Proves the structural separation required by
    ADR 004: the heartbeat loop is not blocked by the execution body."""
    from app.config import settings

    monkeypatch.setattr(settings, "lease_duration_seconds", 1)
    monkeypatch.setattr(settings, "heartbeat_interval_seconds", 1)

    job = service.create_job(db_session, JobCreate(job_type="sft", config={"sleep_seconds": 3}))

    outcome = process_job_message(db_session, job.id, worker_id="w1")

    assert outcome == "claimed"
    final = repo.get(db_session, job.id)
    assert final.status == JobStatus.SUCCEEDED.value
    assert final.attempt_number == 1  # never reclaimed despite a 3s body vs 1s lease

    # Confirm Recovery agrees nothing was ever stale during that window.
    assert reclaim_stale_leases(db_session) == 0
