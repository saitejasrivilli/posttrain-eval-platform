import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import evaluation_run as _evaluation_run  # noqa: F401
from app.models.evaluation_result import EvaluationResult

# Fencing-conditioned (ADR 016) AND idempotent. WHERE EXISTS enforces live
# ownership; ON CONFLICT DO NOTHING makes duplicate delivery / a retried
# attempt re-emitting the same example a no-op instead of a duplicate logical
# row (FAILURE_SCENARIOS_V0.7.md #7, #13).
_RECORD_SQL = text(
    """
    INSERT INTO evaluation_results
        (evaluation_run_id, example_id, prediction, expected_output, score,
         latency_ms, error_code, error_message, attempt_number, created_at)
    SELECT :evaluation_run_id, :example_id, :prediction, :expected_output, :score,
           :latency_ms, :error_code, :error_message, :attempt_number, :created_at
    WHERE EXISTS (
        SELECT 1 FROM jobs
        WHERE id = :job_id AND status = 'RUNNING'
          AND lease_owner = :worker_id AND attempt_number = :attempt_number
    )
    ON CONFLICT (evaluation_run_id, example_id) DO NOTHING
    """
)


def record(
    db: Session,
    job_id: uuid.UUID,
    worker_id: str,
    evaluation_run_id: uuid.UUID,
    attempt_number: int,
    example_id: str,
    prediction: str | None,
    expected_output: str | None,
    score: float | None,
    latency_ms: float | None,
    error_code: str | None = None,
    error_message: str | None = None,
    commit: bool = True,
) -> bool:
    """Returns True iff a new row was inserted (fencing passed AND not a
    duplicate). False means either fenced out or an idempotent no-op."""
    result = db.execute(
        _RECORD_SQL,
        {
            "evaluation_run_id": str(evaluation_run_id),
            "example_id": example_id,
            "prediction": prediction,
            "expected_output": expected_output,
            "score": score,
            "latency_ms": latency_ms,
            "error_code": error_code,
            "error_message": error_message,
            "attempt_number": attempt_number,
            "created_at": datetime.now(timezone.utc),
            "job_id": str(job_id),
            "worker_id": worker_id,
        },
    )
    if commit:
        db.commit()
    return result.rowcount > 0


def list_for_run(
    db: Session, evaluation_run_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[EvaluationResult], int]:
    base = db.query(EvaluationResult).filter(EvaluationResult.evaluation_run_id == evaluation_run_id)
    total = base.count()
    items = base.order_by(EvaluationResult.example_id.asc()).limit(limit).offset(offset).all()
    return items, total


def count_for_run(db: Session, evaluation_run_id: uuid.UUID) -> int:
    return (
        db.query(EvaluationResult)
        .filter(EvaluationResult.evaluation_run_id == evaluation_run_id)
        .count()
    )
