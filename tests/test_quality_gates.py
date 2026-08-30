"""V0.7 quality-gate + determinism + baseline tests.

Covers: deterministic metric calculation (tested directly), gate PASS/FAIL,
missing-metric => ERROR, ERROR never becomes PASS, duplicate gate evaluation
idempotent, gate rejected on non-SUCCEEDED run, baseline dataset/config
mismatch rejected, and a baseline-delta gate (FAILURE_SCENARIOS_V0.7.md
#16-#20, #22-#24).
"""
import json
import uuid

import pytest
from fastapi import HTTPException

from app.evaluation import quality_gate_engine as engine
from app.evaluation.metrics import aggregate, exact_match, token_accuracy
from app.repository import evaluation_configs as configs_repo
from app.repository import quality_gate_results as gate_results_repo
from app.repository import quality_gates as gates_repo
from app.schemas import EvaluationCreate
from app.services import datasets as datasets_service
from app.services import evaluations as eval_service
from app.services.worker import process_job_message
from app.repository import jobs as repo
from tests.conftest import reserve_for_claim
from tests.test_evaluation_fencing import (
    _make_dataset_version, _make_model_version, make_evaluation,
)

EXAMPLES = [
    {"id": "e1", "input": "hello world", "expected_output": "hello world"},
    {"id": "e2", "input": "foo bar", "expected_output": "foo bar"},
    {"id": "e3", "input": "one two three", "expected_output": "one two three"},
    {"id": "e4", "input": "mismatch here", "expected_output": "totally different"},
]


def _run_to_success(db, storage, run):
    job = repo.get(db, run.job_id)
    reserve_for_claim(db, job)
    outcome = process_job_message(db, run.job_id, worker_id="w1", storage_client=storage)
    assert outcome == "claimed"
    return run


# --- Determinism (tested directly on the pure functions) ---

def test_metric_functions_are_deterministic():
    assert exact_match("a b", "a b") == exact_match("a b", "a b") == 1.0
    assert exact_match("a b", "a c") == 0.0
    assert token_accuracy("a b c", "a x c") == token_accuracy("a b c", "a x c") == pytest.approx(2 / 3)

    per_example = [
        {"exact_match": 1.0, "token_accuracy": 1.0, "latency_ms": 1.0},
        {"exact_match": 0.0, "token_accuracy": 0.5, "latency_ms": 2.0},
    ]
    first = aggregate(per_example)
    second = aggregate(per_example)
    assert first == second
    em = {m["metric_name"]: m["metric_value"] for m in first}["exact_match"]
    assert em == 0.5


def test_same_inputs_produce_same_metrics_across_two_runs(db_session, storage_client):
    run_a = _run_to_success(db_session, storage_client,
                            make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0))
    run_b = _run_to_success(db_session, storage_client,
                            make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0))
    from app.repository import evaluation_metrics as metrics_repo
    a = metrics_repo.metric_map(db_session, run_a.id)
    b = metrics_repo.metric_map(db_session, run_b.id)
    assert a["exact_match"] == b["exact_match"] == 0.75
    assert a["token_accuracy"] == b["token_accuracy"]


# --- Gate engine (pure) ---

def test_gate_pass():
    status, results = engine.evaluate(
        {"all": [{"metric": "exact_match", "operator": ">=", "value": 0.5}]},
        {"exact_match": 0.75},
    )
    assert status == engine.PASS


def test_gate_fail():
    status, _ = engine.evaluate(
        {"all": [{"metric": "exact_match", "operator": ">=", "value": 0.9}]},
        {"exact_match": 0.75},
    )
    assert status == engine.FAIL


def test_missing_metric_is_error_not_pass():
    status, results = engine.evaluate(
        {"all": [{"metric": "latency_p95_ms", "operator": "<=", "value": 500}]},
        {"exact_match": 0.75},  # latency_p95_ms absent
    )
    assert status == engine.ERROR
    assert results[0]["status"] == engine.ERROR


def test_error_never_becomes_pass_under_all():
    # One passing rule, one erroring (missing) rule -> gate must be ERROR.
    status, _ = engine.evaluate(
        {"all": [
            {"metric": "exact_match", "operator": ">=", "value": 0.5},
            {"metric": "does_not_exist", "operator": ">=", "value": 0.5},
        ]},
        {"exact_match": 0.75},
    )
    assert status == engine.ERROR


def test_invalid_operator_is_error():
    status, _ = engine.evaluate(
        {"all": [{"metric": "exact_match", "operator": "≥", "value": 0.5}]},
        {"exact_match": 0.75},
    )
    assert status == engine.ERROR


# --- Gate persistence via the service ---

def test_gate_evaluation_persists_pass(db_session, storage_client):
    run = _run_to_success(db_session, storage_client,
                          make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0))
    gate = gates_repo.create(db_session, "release-gate",
                             {"all": [{"metric": "exact_match", "operator": ">=", "value": 0.5}]})
    result, inserted = eval_service.evaluate_quality_gate(db_session, run.id, gate.id)
    assert result.status == "PASS"
    assert inserted is True


def test_duplicate_gate_evaluation_is_idempotent(db_session, storage_client):
    run = _run_to_success(db_session, storage_client,
                          make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0))
    gate = gates_repo.create(db_session, "g",
                             {"all": [{"metric": "exact_match", "operator": ">=", "value": 0.5}]})
    _, first = eval_service.evaluate_quality_gate(db_session, run.id, gate.id)
    _, second = eval_service.evaluate_quality_gate(db_session, run.id, gate.id)
    assert first is True
    assert second is False  # idempotent: no second logical decision
    assert len(gate_results_repo.list_for_run(db_session, run.id)) == 1


def test_gate_rejected_when_run_not_succeeded(db_session, storage_client):
    run = make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0)  # QUEUED, never ran
    gate = gates_repo.create(db_session, "g",
                             {"all": [{"metric": "exact_match", "operator": ">=", "value": 0.5}]})
    with pytest.raises(HTTPException) as exc:
        eval_service.evaluate_quality_gate(db_session, run.id, gate.id)
    assert exc.value.status_code == 409


def test_gate_missing_metric_persists_error(db_session, storage_client):
    run = _run_to_success(db_session, storage_client,
                          make_evaluation(db_session, storage_client, examples=EXAMPLES, param=1.0))
    gate = gates_repo.create(db_session, "g",
                             {"all": [{"metric": "nonexistent_metric", "operator": ">=", "value": 0.5}]})
    result, _ = eval_service.evaluate_quality_gate(db_session, run.id, gate.id)
    assert result.status == "ERROR"


# --- Baseline comparison ---

def _make_baseline_and_candidate(db, storage, same_dataset: bool, same_config: bool):
    """Build a SUCCEEDED baseline evaluation and a candidate evaluation whose
    baseline refs point at it. Optionally diverge dataset/config."""
    # Shared dataset version.
    ds, dv = _make_dataset_version(db, storage, EXAMPLES)
    base_model, base_mv = _make_model_version(db, storage, param=1.0)
    cand_model, cand_mv = _make_model_version(db, storage, param=1.0)

    base_cfg = configs_repo.create(db, task_type="text", metric_definitions={}, batch_size=1,
                                   evaluator_code_commit="c", container_image="i")
    cand_cfg = base_cfg if same_config else configs_repo.create(
        db, task_type="text", metric_definitions={"variant": "other"}, batch_size=1,
        evaluator_code_commit="c", container_image="i")

    # Baseline run on ds/dv with base_cfg -> SUCCEEDED.
    base_run = eval_service.create_evaluation(db, EvaluationCreate(
        model_id=base_model.id, model_version_number=base_mv.version_number,
        dataset_id=ds.id, dataset_version_number=dv.version_number,
        evaluation_config_id=base_cfg.id))
    _run_to_success(db, storage, base_run)

    # Candidate dataset (same or different).
    if same_dataset:
        cds, cdv = ds, dv
    else:
        cds, cdv = _make_dataset_version(db, storage, EXAMPLES)

    cand_run = eval_service.create_evaluation(db, EvaluationCreate(
        model_id=cand_model.id, model_version_number=cand_mv.version_number,
        dataset_id=cds.id, dataset_version_number=cdv.version_number,
        evaluation_config_id=cand_cfg.id,
        baseline_model_id=base_model.id, baseline_model_version_number=base_mv.version_number))
    _run_to_success(db, storage, cand_run)
    return cand_run


def test_baseline_dataset_mismatch_rejected(db_session, storage_client):
    cand = _make_baseline_and_candidate(db_session, storage_client, same_dataset=False, same_config=True)
    with pytest.raises(HTTPException) as exc:
        eval_service.compute_baseline_deltas(db_session, cand)
    assert exc.value.status_code == 409


def test_baseline_config_mismatch_rejected(db_session, storage_client):
    cand = _make_baseline_and_candidate(db_session, storage_client, same_dataset=True, same_config=False)
    with pytest.raises(HTTPException) as exc:
        eval_service.compute_baseline_deltas(db_session, cand)
    assert exc.value.status_code == 409


def test_baseline_delta_gate_and_explicit_deltas(db_session, storage_client):
    cand = _make_baseline_and_candidate(db_session, storage_client, same_dataset=True, same_config=True)
    deltas = eval_service.compute_baseline_deltas(db_session, cand)
    assert deltas is not None
    assert deltas["deltas"]["exact_match"] == 0.0  # identical models on same data

    gate = gates_repo.create(db_session, "no-regression",
                             {"all": [{"metric": "exact_match", "operator": ">=", "baseline_delta": 0.0}]})
    result, _ = eval_service.evaluate_quality_gate(db_session, cand.id, gate.id)
    assert result.status == "PASS"  # delta 0.0 >= 0.0
