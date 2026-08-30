"""End-to-end V0.7 evaluation tests: real subprocess spawned via
`python -m app.evaluation.subprocess_main`, real model + dataset artifact
bytes, real SHA-256 identity verification, real fencing-conditioned
result/metric writes -- using the dependency-free toy evaluator
(app/evaluation/toy_evaluator.py) since this environment has no CUDA GPU
(mirrors tests/test_training_execution.py).
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config import settings
from app.models.job import JobStatus
from app.repository import attempts as attempts_repo
from app.repository import evaluation_metrics as metrics_repo
from app.repository import evaluation_results as results_repo
from app.repository import evaluation_runs as runs_repo
from app.repository import jobs as repo
from app.services.recovery import reclaim_stale_leases
from app.services.worker import process_job_message
from tests.conftest import reserve_for_claim
from tests.test_evaluation_fencing import make_evaluation

EXAMPLES = [
    {"id": "e1", "input": "hello world", "expected_output": "hello world"},
    {"id": "e2", "input": "foo bar", "expected_output": "foo bar"},
    {"id": "e3", "input": "one two three", "expected_output": "one two three"},
    {"id": "e4", "input": "mismatch here", "expected_output": "totally different"},
]


def test_real_subprocess_evaluation_produces_results_and_metrics(db_session, storage_client):
    # param=1.0 -> identity predictions; 3 of 4 examples exact-match.
    run = make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)

    outcome = process_job_message(db_session, run.job_id, worker_id="w1", storage_client=storage_client)
    assert outcome == "claimed"

    final_job = repo.get(db_session, run.job_id)
    assert final_job.status == JobStatus.SUCCEEDED.value
    assert runs_repo.get(db_session, run.id).status == "SUCCEEDED"

    # Per-example results for every example.
    items, total = results_repo.list_for_run(db_session, run.id, limit=100, offset=0)
    assert total == 4
    by_id = {r.example_id: r for r in items}
    assert by_id["e1"].score == 1.0
    assert by_id["e4"].score == 0.0
    assert by_id["e1"].latency_ms is not None

    # Aggregate metrics recorded (result + metric + final events all wired).
    metrics = {m.metric_name: m.metric_value for m in metrics_repo.list_for_run(db_session, run.id)}
    assert metrics["exact_match"] == 0.75  # 3 of 4
    assert "token_accuracy" in metrics
    assert "latency_p95_ms" in metrics
    assert metrics["exact_match"] > 0.0


def test_corrupt_model_artifact_fails_closed(db_session, storage_client):
    run = make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0)
    # Corrupt the stored model object after registration: the subprocess
    # re-hashes local bytes vs the registered content hash and fails closed.
    model_version = runs_repo.get(db_session, run.id)
    from app.repository import models as models_repo
    mv = models_repo.get_version(db_session, model_version.model_id, model_version.model_version_number)
    from app.repository import artifacts as artifacts_repo
    artifact = artifacts_repo.get(db_session, mv.artifact_id)
    storage_client.put_object(Bucket=settings.minio_bucket, Key=artifact.storage_key, Body=b"corrupted-not-json")

    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)
    outcome = process_job_message(db_session, run.job_id, worker_id="w1", storage_client=storage_client)
    assert outcome == "claimed"

    # No metrics, no SUCCEEDED status -- fail closed (FAILURE_SCENARIOS #11).
    assert metrics_repo.list_for_run(db_session, run.id) == []
    assert runs_repo.get(db_session, run.id).status != "SUCCEEDED"


def test_evaluator_crash_then_retry_recovers(db_session, storage_client):
    """Evaluator 'crash' recovered by V0.3 Recovery (unmodified), then a retry
    completes -- mirrors test_worker_kill_then_retry_resumes_from_checkpoint's
    shape. Attempt 1 is manually claimed and abandoned (lease expiry); Recovery
    reclaims; attempt 2 runs the real subprocess to SUCCEEDED."""
    run = make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)

    claimed = repo.claim(db_session, run.job_id, "worker-A", lease_duration_seconds=30)
    assert claimed is not None
    attempts_repo.insert(db_session, run.job_id, 1, "worker-A")
    # worker-A "crashes" -> lease expires without finalizing.
    db_session.execute(
        text("UPDATE jobs SET lease_expires_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "id": str(run.job_id)},
    )
    db_session.commit()
    assert reclaim_stale_leases(db_session) == 1
    assert repo.get(db_session, run.job_id).status == JobStatus.QUEUED.value
    db_session.execute(text("UPDATE jobs SET next_retry_at = now() WHERE id = :id"), {"id": str(run.job_id)})
    db_session.commit()

    # Attempt 2: real subprocess run completes.
    reserve_for_claim(db_session, repo.get(db_session, run.job_id))
    outcome = process_job_message(db_session, run.job_id, worker_id="w2", storage_client=storage_client)
    assert outcome == "claimed"

    final_job = repo.get(db_session, run.job_id)
    assert final_job.status == JobStatus.SUCCEEDED.value
    assert final_job.attempt_number == 2
    assert runs_repo.get(db_session, run.id).status == "SUCCEEDED"
    _, total = results_repo.list_for_run(db_session, run.id, limit=100, offset=0)
    assert total == 4
