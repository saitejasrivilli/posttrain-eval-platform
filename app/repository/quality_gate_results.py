import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import evaluation_run as _evaluation_run  # noqa: F401
from app.models import quality_gate as _quality_gate  # noqa: F401
from app.models.quality_gate_result import QualityGateResult

# A gate result may be recorded only against a run that is actually in
# terminal SUCCEEDED state (a gate is a policy over a *completed* run --
# QUALITY_GATE_MODEL_V0.7.md / ADR 019). This is the fence for gate writes:
# a gate decision computed against a run that is not a valid completed run is
# rejected, and ON CONFLICT DO NOTHING makes a duplicate gate evaluation an
# idempotent no-op (FAILURE_SCENARIOS_V0.7.md #20). Gate evaluation is a
# synchronous policy operation, not a leased job (see report/ADR 019).
_RECORD_SQL = text(
    """
    INSERT INTO quality_gate_results
        (id, evaluation_run_id, quality_gate_id, status, rule_results, evaluated_at)
    SELECT :id, :evaluation_run_id, :quality_gate_id, :status,
           CAST(:rule_results AS JSONB), :evaluated_at
    WHERE EXISTS (
        SELECT 1 FROM evaluation_runs
        WHERE id = :evaluation_run_id AND status = 'SUCCEEDED'
    )
    ON CONFLICT (evaluation_run_id, quality_gate_id) DO NOTHING
    """
)


def record(
    db: Session,
    evaluation_run_id: uuid.UUID,
    quality_gate_id: uuid.UUID,
    status: str,
    rule_results: list | dict,
) -> bool:
    """Returns True iff a new gate result row was inserted (run is SUCCEEDED
    AND not a duplicate). False means the run was not in a valid completed
    state, or the gate was already evaluated (idempotent no-op)."""
    result = db.execute(
        _RECORD_SQL,
        {
            "id": str(uuid.uuid4()),
            "evaluation_run_id": str(evaluation_run_id),
            "quality_gate_id": str(quality_gate_id),
            "status": status,
            "rule_results": json.dumps(rule_results),
            "evaluated_at": datetime.now(timezone.utc),
        },
    )
    db.commit()
    return result.rowcount > 0


def get(db: Session, evaluation_run_id: uuid.UUID, quality_gate_id: uuid.UUID) -> QualityGateResult | None:
    return (
        db.query(QualityGateResult)
        .filter(
            QualityGateResult.evaluation_run_id == evaluation_run_id,
            QualityGateResult.quality_gate_id == quality_gate_id,
        )
        .one_or_none()
    )


def list_for_run(db: Session, evaluation_run_id: uuid.UUID) -> list[QualityGateResult]:
    return (
        db.query(QualityGateResult)
        .filter(QualityGateResult.evaluation_run_id == evaluation_run_id)
        .order_by(QualityGateResult.evaluated_at.desc())
        .all()
    )
