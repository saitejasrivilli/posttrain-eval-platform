"""Release-blocking test tier for V0.7 (same tier as V0.6's checkpoint
split-brain tests, tests/test_checkpoint_fencing.py). A stale evaluator whose
lease was reclaimed must NEVER be able to:
  - flip the EvaluationRun to SUCCEEDED or FAILED,
  - write a per-example result,
  - write an aggregate metric,
  - complete an evaluation after reclamation,
even though the fencing is the SECOND layer (ADR 016): the evaluator's
subprocess may have run fine, but its DB writes are conditioned on live job
ownership (status='RUNNING' AND lease_owner AND attempt_number).

Also covers duplicate-delivery idempotency and the cancellation-vs-completion
race (FAILURE_SCENARIOS_V0.7.md #6, #7, #13, #15, #21).
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.repository import attempts as attempts_repo
from app.repository import evaluation_configs as configs_repo
from app.repository import evaluation_metrics as metrics_repo
from app.repository import evaluation_results as results_repo
from app.repository import evaluation_runs as runs_repo
from app.repository import jobs as repo
from app.schemas import EvaluationCreate
from app.services import artifacts as artifacts_service
from app.services import datasets as datasets_service
from app.services import evaluations as eval_service
from app.services import models as models_service
from app.services.recovery import reclaim_stale_leases
from tests.conftest import reserve_for_claim


def _make_model_version(db, storage, param=1.0):
    model = models_service.create_model(db, f"model-{uuid.uuid4()}", None)
    # Include a unique nonce so distinct models never dedupe to the same
    # artifact (an artifact maps to at most one ModelVersion, ADR 010/V0.5).
    # The evaluator reads only "param"; extra fields are inert.
    art = artifacts_service.upload_artifact(
        db, storage, json.dumps({"param": param, "nonce": str(uuid.uuid4())}).encode(), "MODEL", "u1"
    )
    mv = models_service.register_version(db, model.id, art.id, None)
    return model, mv


def _make_dataset_version(db, storage, examples):
    ds = datasets_service.create_dataset(db, f"ds-{uuid.uuid4()}", None)
    dv = datasets_service.create_dataset_version(
        db, storage, ds.id, json.dumps({"examples": examples}).encode(), "u1"
    )
    return ds, dv


def make_evaluation(db, storage, examples=None, param=1.0):
    examples = examples or [{"id": "e1", "input": "a b", "expected_output": "a b"}]
    model, mv = _make_model_version(db, storage, param=param)
    ds, dv = _make_dataset_version(db, storage, examples)
    cfg = configs_repo.create(
        db, task_type="text", metric_definitions={}, batch_size=1,
        evaluator_code_commit="evalcommit", container_image="eval:latest",
    )
    payload = EvaluationCreate(
        model_id=model.id, model_version_number=mv.version_number,
        dataset_id=ds.id, dataset_version_number=dv.version_number,
        evaluation_config_id=cfg.id,
    )
    return eval_service.create_evaluation(db, payload)


def _claim(db, run, worker="worker-A", attempt=1):
    job = repo.get(db, run.job_id)
    reserve_for_claim(db, job)
    claimed = repo.claim(db, run.job_id, worker, lease_duration_seconds=30)
    assert claimed is not None
    attempts_repo.insert(db, run.job_id, attempt, worker)
    return claimed


def test_stale_evaluator_cannot_write_success(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run)
    assert repo.finalize_attempt(db_session, run.job_id, "worker-A", 1, "FAILED") is not None

    ok = runs_repo.mark_status(db_session, run.id, run.job_id, "worker-A", 1, "SUCCEEDED", set_completed=True)
    assert ok is False
    assert runs_repo.get(db_session, run.id).status != "SUCCEEDED"


def test_stale_evaluator_cannot_write_failed(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run)
    # Job finalized to SUCCEEDED by the live attempt; a stale write to FAILED
    # must not stick.
    assert repo.finalize_attempt(db_session, run.job_id, "worker-A", 1, "SUCCEEDED") is not None
    ok = runs_repo.mark_status(db_session, run.id, run.job_id, "worker-A", 1, "FAILED", set_completed=False)
    assert ok is False


def test_stale_evaluator_cannot_write_result(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run)
    assert repo.finalize_attempt(db_session, run.job_id, "worker-A", 1, "FAILED") is not None

    ok = results_repo.record(
        db_session, run.job_id, "worker-A", run.id, 1, example_id="e1",
        prediction="a b", expected_output="a b", score=1.0, latency_ms=0.5,
    )
    assert ok is False
    assert results_repo.count_for_run(db_session, run.id) == 0


def test_stale_evaluator_cannot_write_metric(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run)
    assert repo.finalize_attempt(db_session, run.job_id, "worker-A", 1, "FAILED") is not None

    ok = metrics_repo.record(
        db_session, run.job_id, "worker-A", run.id, 1,
        metric_name="exact_match", metric_value=1.0, split="all", sample_count=1,
    )
    assert ok is False
    assert metrics_repo.list_for_run(db_session, run.id) == []


def test_stale_evaluator_cannot_complete_after_reclamation(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run, worker="worker-A", attempt=1)

    # worker-A's lease expires; Recovery reclaims -> job QUEUED, attempt bumps.
    db_session.execute(
        text("UPDATE jobs SET lease_expires_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "id": str(run.job_id)},
    )
    db_session.commit()
    assert reclaim_stale_leases(db_session) == 1
    db_session.execute(text("UPDATE jobs SET next_retry_at = now() WHERE id = :id"), {"id": str(run.job_id)})
    db_session.commit()

    # A fresh attempt claims the job.
    reserve_for_claim(db_session, repo.get(db_session, run.job_id))
    claimed = repo.claim(db_session, run.job_id, "worker-B", lease_duration_seconds=30)
    assert claimed is not None
    assert claimed.attempt_number == 2

    # The stale worker-A (attempt 1) tries to complete -- rejected on all writes.
    assert runs_repo.mark_status(db_session, run.id, run.job_id, "worker-A", 1, "SUCCEEDED", set_completed=True) is False
    assert results_repo.record(
        db_session, run.job_id, "worker-A", run.id, 1, example_id="e1",
        prediction="x", expected_output="a b", score=0.0, latency_ms=0.1,
    ) is False
    assert metrics_repo.record(
        db_session, run.job_id, "worker-A", run.id, 1,
        metric_name="exact_match", metric_value=0.0, split="all", sample_count=1,
    ) is False
    assert runs_repo.get(db_session, run.id).status != "SUCCEEDED"


def test_wrong_attempt_number_cannot_write(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run, worker="worker-A", attempt=1)
    # Same worker, still RUNNING, but a stale/superseded attempt_number.
    assert results_repo.record(
        db_session, run.job_id, "worker-A", run.id, attempt_number=99, example_id="e1",
        prediction="a b", expected_output="a b", score=1.0, latency_ms=0.1,
    ) is False
    assert metrics_repo.record(
        db_session, run.job_id, "worker-A", run.id, attempt_number=99,
        metric_name="exact_match", metric_value=1.0, split="all", sample_count=1,
    ) is False


def test_duplicate_result_delivery_is_idempotent(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run)
    first = results_repo.record(
        db_session, run.job_id, "worker-A", run.id, 1, example_id="e1",
        prediction="a b", expected_output="a b", score=1.0, latency_ms=0.5,
    )
    second = results_repo.record(
        db_session, run.job_id, "worker-A", run.id, 1, example_id="e1",
        prediction="a b", expected_output="a b", score=1.0, latency_ms=0.6,
    )
    assert first is True
    assert second is False  # idempotent no-op, not a duplicate logical row
    assert results_repo.count_for_run(db_session, run.id) == 1


def test_duplicate_metric_delivery_is_idempotent(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run)
    first = metrics_repo.record(
        db_session, run.job_id, "worker-A", run.id, 1,
        metric_name="exact_match", metric_value=1.0, split="all", sample_count=1,
    )
    second = metrics_repo.record(
        db_session, run.job_id, "worker-A", run.id, 1,
        metric_name="exact_match", metric_value=0.5, split="all", sample_count=1,
    )
    assert first is True
    assert second is False
    rows = metrics_repo.list_for_run(db_session, run.id)
    assert len(rows) == 1
    assert rows[0].metric_value == 1.0  # first write wins; not overwritten


def test_cancellation_cannot_be_overwritten_by_stale_completion(db_session, storage_client):
    run = make_evaluation(db_session, storage_client)
    _claim(db_session, run)
    # Cancellation wins via the fenced terminal job write.
    assert repo.finalize_attempt(db_session, run.job_id, "worker-A", 1, "CANCELLED") is not None
    # A late completion write from the (now stale) attempt is rejected.
    assert runs_repo.mark_status(db_session, run.id, run.job_id, "worker-A", 1, "SUCCEEDED", set_completed=True) is False
    assert runs_repo.get(db_session, run.id).status != "SUCCEEDED"
