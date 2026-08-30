import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.quality_gate import QualityGate


def create(db: Session, name: str, rules: dict) -> QualityGate:
    """Immutable declarative policy (ADR 019). No update path after creation."""
    gate = QualityGate(name=name, rules=rules, created_at=datetime.now(timezone.utc))
    db.add(gate)
    db.commit()
    db.refresh(gate)
    return gate


def get(db: Session, gate_id: uuid.UUID) -> QualityGate | None:
    return db.query(QualityGate).filter(QualityGate.id == gate_id).one_or_none()
