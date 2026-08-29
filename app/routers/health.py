from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import health as service

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz(response: Response, db: Session = Depends(get_db)):
    if not service.is_ready(db):
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ready"}
