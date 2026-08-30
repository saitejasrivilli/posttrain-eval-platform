import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import evaluation_run as _evaluation_run  # noqa: F401
from app.models.evaluation_metric import EvaluationMetric

# Fencing-conditioned (ADR 016) AND idempotent on (run, metric_name, split).
# A retry that recomputes aggregates fills in any missing metric without
# rewriting an already-persisted one (FAILURE_SCENARIOS_V0.7.md #14).
_RECORD_SQL = text(
    """
    INSERT INTO evaluation_metrics
        (evaluation_run_id, metric_name, split, metric_value, sample_count,
         attempt_number, created_at)
    SELECT :evaluation_run_id, :metric_name, :split, :metric_value, :sample_count,
           :attempt_number, :created_at
    WHERE EXISTS (
        SELECT 1 FROM jobs
        WHERE id = :job_id AND status = 'RUNNING'
          AND lease_owner = :worker_id AND attempt_number = :attempt_number
    )
    ON CONFLICT (evaluation_run_id, metric_name, split) DO NOTHING
    """
)


def record(
    db: Session,
    job_id: uuid.UUID,
    worker_id: str,
    evaluation_run_id: uuid.UUID,
    attempt_number: int,
    metric_name: str,
    metric_value: float,
    split: str,
    sample_count: int,
    commit: bool = True,
) -> bool:
    """Returns True iff a new metric row was inserted (fencing passed AND not
    a duplicate). False means fenced out or idempotent no-op."""
    result = db.execute(
        _RECORD_SQL,
        {
            "evaluation_run_id": str(evaluation_run_id),
            "metric_name": metric_name,
            "split": split,
            "metric_value": metric_value,
            "sample_count": sample_count,
            "attempt_number": attempt_number,
            "created_at": datetime.now(timezone.utc),
            "job_id": str(job_id),
            "worker_id": worker_id,
        },
    )
    if commit:
        db.commit()
    return result.rowcount > 0


def list_for_run(db: Session, evaluation_run_id: uuid.UUID) -> list[EvaluationMetric]:
    return (
        db.query(EvaluationMetric)
        .filter(EvaluationMetric.evaluation_run_id == evaluation_run_id)
        .order_by(EvaluationMetric.split.asc(), EvaluationMetric.metric_name.asc())
        .all()
    )


def metric_map(db: Session, evaluation_run_id: uuid.UUID, split: str = "all") -> dict[str, float]:
    """Persisted-metrics view used by quality-gate evaluation. Reads only
    already-durable metric rows -- never recomputes hidden values (ADR 019)."""
    rows = (
        db.query(EvaluationMetric)
        .filter(
            EvaluationMetric.evaluation_run_id == evaluation_run_id,
            EvaluationMetric.split == split,
        )
        .all()
    )
    return {row.metric_name: row.metric_value for row in rows}
