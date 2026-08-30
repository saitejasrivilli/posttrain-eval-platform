import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import (
    EvaluationConfigCreate, EvaluationConfigOut, EvaluationCreate, EvaluationOut,
    QualityGateCreate, QualityGateOut,
)
from app.services import evaluations as service

router = APIRouter(tags=["evaluations"])


# --- Evaluation configs ---

@router.post("/v1/evaluation-configs", response_model=EvaluationConfigOut, status_code=201)
def create_evaluation_config(payload: EvaluationConfigCreate, db: Session = Depends(get_db)):
    return service.create_evaluation_config(db, payload)


@router.get("/v1/evaluation-configs/{config_id}", response_model=EvaluationConfigOut)
def get_evaluation_config(config_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.get_evaluation_config_or_404(db, config_id)


# --- Evaluations ---

@router.post("/v1/evaluations", response_model=EvaluationOut, status_code=201)
def create_evaluation(payload: EvaluationCreate, db: Session = Depends(get_db)):
    return service.create_evaluation(db, payload)


@router.get("/v1/evaluations/{evaluation_id}", response_model=EvaluationOut)
def get_evaluation(evaluation_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.get_evaluation_or_404(db, evaluation_id)


@router.get("/v1/evaluations/{evaluation_id}/metrics")
def get_evaluation_metrics(evaluation_id: uuid.UUID, db: Session = Depends(get_db)):
    run = service.get_evaluation_or_404(db, evaluation_id)
    metrics = service.list_metrics(db, evaluation_id)
    body = {
        "evaluation_run_id": str(evaluation_id),
        "status": run.status,
        "metrics": [
            {
                "metric_name": m.metric_name, "split": m.split, "metric_value": m.metric_value,
                "sample_count": m.sample_count, "attempt_number": m.attempt_number,
                "created_at": m.created_at,
            }
            for m in metrics
        ],
    }
    if run.baseline_model_id is not None:
        body["baseline_comparison"] = service.compute_baseline_deltas(db, run)
    return body


@router.get("/v1/evaluations/{evaluation_id}/results")
def get_evaluation_results(
    evaluation_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = service.list_results(db, evaluation_id, limit, offset)
    return {
        "items": [
            {
                "example_id": r.example_id, "prediction": r.prediction,
                "expected_output": r.expected_output, "score": r.score,
                "latency_ms": r.latency_ms, "error_code": r.error_code,
                "error_message": r.error_message, "attempt_number": r.attempt_number,
                "created_at": r.created_at,
            }
            for r in items
        ],
        "limit": limit, "offset": offset, "total": total,
    }


@router.get("/v1/evaluations/{evaluation_id}/quality-gates")
def get_evaluation_quality_gates(evaluation_id: uuid.UUID, db: Session = Depends(get_db)):
    results = service.list_quality_gate_results(db, evaluation_id)
    return {
        "evaluation_run_id": str(evaluation_id),
        "quality_gate_results": [
            {
                "id": str(r.id), "quality_gate_id": str(r.quality_gate_id),
                "status": r.status, "rule_results": r.rule_results, "evaluated_at": r.evaluated_at,
            }
            for r in results
        ],
    }


@router.post("/v1/evaluations/{evaluation_id}/quality-gates/{gate_id}/evaluate")
def evaluate_quality_gate(evaluation_id: uuid.UUID, gate_id: uuid.UUID, db: Session = Depends(get_db)):
    result, inserted = service.evaluate_quality_gate(db, evaluation_id, gate_id)
    return {
        "id": str(result.id),
        "evaluation_run_id": str(result.evaluation_run_id),
        "quality_gate_id": str(result.quality_gate_id),
        "status": result.status,
        "rule_results": result.rule_results,
        "evaluated_at": result.evaluated_at,
        "newly_evaluated": inserted,
    }


# --- Quality gates ---

@router.post("/v1/quality-gates", response_model=QualityGateOut, status_code=201)
def create_quality_gate(payload: QualityGateCreate, db: Session = Depends(get_db)):
    return service.create_quality_gate(db, payload)
