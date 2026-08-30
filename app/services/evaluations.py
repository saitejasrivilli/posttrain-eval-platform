import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.evaluation import quality_gate_engine
from app.repository import artifacts as artifacts_repo
from app.repository import datasets as datasets_repo
from app.repository import evaluation_configs as configs_repo
from app.repository import evaluation_metrics as metrics_repo
from app.repository import evaluation_results as results_repo
from app.repository import evaluation_runs as runs_repo
from app.repository import models as models_repo
from app.repository import quality_gate_results as gate_results_repo
from app.repository import quality_gates as gates_repo
from app.schemas import JobCreate
from app.services import jobs as jobs_service


# --- EvaluationConfig ---

def create_evaluation_config(db: Session, payload) -> object:
    return configs_repo.create(
        db,
        task_type=payload.task_type,
        metric_definitions=payload.metric_definitions,
        batch_size=payload.batch_size,
        evaluator_code_commit=payload.evaluator_code_commit,
        container_image=payload.container_image,
        max_examples=payload.max_examples,
        max_sequence_length=payload.max_sequence_length,
        random_seed=payload.random_seed,
    )


def get_evaluation_config_or_404(db: Session, config_id: uuid.UUID):
    config = configs_repo.get(db, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="evaluation config not found")
    return config


# --- EvaluationRun ---

def create_evaluation(db: Session, payload):
    """Creates a Job via the existing, UNMODIFIED V0.2 job-creation path, plus
    an evaluation_runs row referencing it -- exactly mirroring
    training_runs.create_training_run (ARCHITECTURE_V0.7.md / ADR 017). The
    job then flows through the existing V0.2/V0.3/V0.4 pipeline untouched."""
    config = get_evaluation_config_or_404(db, payload.evaluation_config_id)

    model_version = models_repo.get_version(db, payload.model_id, payload.model_version_number)
    if model_version is None:
        raise HTTPException(status_code=404, detail="model version not found")
    # Reject missing/non-UPLOADED model artifacts (API_CHANGES_V0.7.md).
    artifact = artifacts_repo.get(db, model_version.artifact_id)
    if artifact is None or artifact.status != "UPLOADED":
        raise HTTPException(
            status_code=409,
            detail={"message": "model artifact is not UPLOADED, cannot evaluate",
                    "artifact_id": str(model_version.artifact_id)},
        )

    dataset_version = datasets_repo.get_version(db, payload.dataset_id, payload.dataset_version_number)
    if dataset_version is None:
        raise HTTPException(status_code=404, detail="dataset version not found")

    if payload.baseline_model_id is not None:
        baseline_version = models_repo.get_version(
            db, payload.baseline_model_id, payload.baseline_model_version_number
        )
        if baseline_version is None:
            raise HTTPException(status_code=404, detail="baseline model version not found")

    job = jobs_service.create_job(
        db, JobCreate(job_type=payload.job_type, config=payload.job_config, priority=payload.priority)
    )

    return runs_repo.create(
        db,
        job_id=job.id,
        model_id=payload.model_id,
        model_version_number=payload.model_version_number,
        dataset_id=payload.dataset_id,
        dataset_version_number=payload.dataset_version_number,
        evaluation_config_id=payload.evaluation_config_id,
        evaluator_code_commit=config.evaluator_code_commit,
        container_image=config.container_image,
        baseline_model_id=payload.baseline_model_id,
        baseline_model_version_number=payload.baseline_model_version_number,
    )


def get_evaluation_or_404(db: Session, evaluation_run_id: uuid.UUID):
    run = runs_repo.get(db, evaluation_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    return run


def list_metrics(db: Session, evaluation_run_id: uuid.UUID):
    get_evaluation_or_404(db, evaluation_run_id)
    return metrics_repo.list_for_run(db, evaluation_run_id)


def list_results(db: Session, evaluation_run_id: uuid.UUID, limit: int, offset: int):
    get_evaluation_or_404(db, evaluation_run_id)
    return results_repo.list_for_run(db, evaluation_run_id, limit, offset)


# --- Baseline comparison ---

def _config_compatible(db, candidate_config_id, baseline_config_id) -> bool:
    if candidate_config_id == baseline_config_id:
        return True
    cand = configs_repo.get(db, candidate_config_id)
    base = configs_repo.get(db, baseline_config_id)
    if cand is None or base is None:
        return False
    return cand.task_type == base.task_type and cand.metric_definitions == base.metric_definitions


def resolve_baseline_metrics(db: Session, run, strict: bool) -> dict | None:
    """Locate the baseline ModelVersion's completed EvaluationRun metrics,
    validating it used the SAME DatasetVersion and a COMPATIBLE
    EvaluationConfig (EVALUATION_MODEL_V0.7.md / FAILURE_SCENARIOS_V0.7.md
    #17, #18). Returns the baseline metric map, or None. When strict, an
    incompatible/missing baseline raises 409 (reject, never silently proceed).
    """
    if run.baseline_model_id is None:
        return None

    candidates = runs_repo.list_for_model_version(
        db, run.baseline_model_id, run.baseline_model_version_number
    )
    completed = [c for c in candidates if c.status == "SUCCEEDED"]
    if not completed:
        if strict:
            raise HTTPException(status_code=409,
                                detail="baseline model version has no completed evaluation to compare against")
        return None

    for base_run in completed:
        dataset_ok = (base_run.dataset_id == run.dataset_id
                      and base_run.dataset_version_number == run.dataset_version_number)
        config_ok = _config_compatible(db, run.evaluation_config_id, base_run.evaluation_config_id)
        if dataset_ok and config_ok:
            return metrics_repo.metric_map(db, base_run.id)

    if strict:
        raise HTTPException(
            status_code=409,
            detail="baseline evaluation used a different DatasetVersion or incompatible EvaluationConfig",
        )
    return None


def compute_baseline_deltas(db: Session, run) -> dict | None:
    """Explicit metric deltas vs a compatible baseline (rejects incompatible
    comparisons). Returns None when no baseline was requested."""
    baseline_metrics = resolve_baseline_metrics(db, run, strict=True)
    if baseline_metrics is None:
        return None
    candidate_metrics = metrics_repo.metric_map(db, run.id)
    deltas = {
        name: candidate_metrics[name] - baseline_metrics[name]
        for name in candidate_metrics
        if name in baseline_metrics
    }
    return {
        "baseline_model_id": str(run.baseline_model_id),
        "baseline_model_version_number": run.baseline_model_version_number,
        "candidate_metrics": candidate_metrics,
        "baseline_metrics": baseline_metrics,
        "deltas": deltas,
    }


# --- Quality gates ---

def create_quality_gate(db: Session, payload):
    return gates_repo.create(db, name=payload.name, rules=payload.rules)


def get_quality_gate_or_404(db: Session, gate_id: uuid.UUID):
    gate = gates_repo.get(db, gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="quality gate not found")
    return gate


def list_quality_gate_results(db: Session, evaluation_run_id: uuid.UUID):
    get_evaluation_or_404(db, evaluation_run_id)
    return gate_results_repo.list_for_run(db, evaluation_run_id)


def evaluate_quality_gate(db: Session, evaluation_run_id: uuid.UUID, gate_id: uuid.UUID):
    """Applies a gate policy to ALREADY-PERSISTED metrics (ADR 019). This is a
    policy operation only -- it never registers/mutates/promotes a model. A
    duplicate evaluation is idempotent (unique (run, gate)). ERROR is never
    converted to PASS."""
    run = get_evaluation_or_404(db, evaluation_run_id)
    gate = get_quality_gate_or_404(db, gate_id)

    if run.status != "SUCCEEDED":
        raise HTTPException(
            status_code=409,
            detail={"message": "evaluation run is not SUCCEEDED; cannot evaluate quality gate",
                    "status": run.status},
        )

    metrics = metrics_repo.metric_map(db, run.id)
    # Non-strict: an incompatible/missing baseline yields ERROR via the engine,
    # never a silent PASS (FAILURE_SCENARIOS_V0.7.md #16, #17, #18).
    baseline_metrics = resolve_baseline_metrics(db, run, strict=False)

    status, rule_results = quality_gate_engine.evaluate(gate.rules, metrics, baseline_metrics)

    inserted = gate_results_repo.record(db, run.id, gate.id, status, rule_results)
    existing = gate_results_repo.get(db, run.id, gate.id)
    if existing is None:
        # Fenced out: run left SUCCEEDED underneath us (should not happen given
        # the check above, but the write is the authority).
        raise HTTPException(status_code=409, detail="evaluation run is no longer in a valid completed state")
    return existing, inserted
