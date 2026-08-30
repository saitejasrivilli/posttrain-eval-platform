"""V0.8: /metrics endpoint tests.

These assert that metrics reflect REAL persisted state (a real job run through
the real worker code path), not fabricated numbers -- the counter must go up
after a real job actually completes.
"""
import re
import uuid

from app.models.job import JobStatus
from app.repository import jobs as repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services.worker import process_job_message
from tests.conftest import reserve_for_claim


def _metric_value(body: str, name: str, labels: str = "") -> float:
    """Parse a single sample value from Prometheus text output."""
    pattern = rf"^{re.escape(name)}{re.escape(labels)}\s+([0-9.eE+-]+)$"
    for line in body.splitlines():
        m = re.match(pattern, line)
        if m:
            return float(m.group(1))
    return 0.0


def test_metrics_endpoint_exposes_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    # A representative sample of the required metric families are present.
    for name in (
        "jobs_created_total",
        "jobs_completed_total",
        "jobs_failed_total",
        "jobs_retried_total",
        "job_queue_depth",
        "worker_active_jobs",
        "outbox_pending",
        "scheduler_reservations",
        "scheduler_capacity_allocated",
        "scheduler_capacity_total",
        "evaluation_runs_total",
        "evaluation_failures_total",
        "checkpoint_created_total",
        "checkpoint_resume_total",
        "job_execution_seconds",
        "job_recovery_seconds",
        "evaluation_duration_seconds",
    ):
        assert name in body, f"missing metric family: {name}"


def test_jobs_completed_counter_increments_after_real_job(client, db_session):
    # Baseline scrape.
    before = _metric_value(
        client.get("/metrics").text, "jobs_completed_total", '{job_type="noop_job"}'
    )

    # Create + run a real job through the actual worker code path to SUCCEEDED.
    job = service.create_job(db_session, JobCreate(job_type="noop_job"))
    reserve_for_claim(db_session, job)
    outcome = process_job_message(db_session, job.id, worker_id="w-metrics")
    assert outcome == "claimed"
    assert repo.get(db_session, job.id).status == JobStatus.SUCCEEDED.value

    after = _metric_value(
        client.get("/metrics").text, "jobs_completed_total", '{job_type="noop_job"}'
    )
    assert after == before + 1, f"expected completed counter to increment ({before} -> {after})"

    # And the execution-time histogram observed at least one real sample.
    body = client.get("/metrics").text
    assert _metric_value(body, "job_execution_seconds_count", "") >= 1
