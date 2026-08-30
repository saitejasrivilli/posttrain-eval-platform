from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.metrics import CONTENT_TYPE, collect_metrics_text

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    """V0.8: Prometheus text-exposition metrics, derived from authoritative
    Postgres state at scrape time (see app/metrics.py for the rationale)."""
    return Response(content=collect_metrics_text(db), media_type=CONTENT_TYPE)
