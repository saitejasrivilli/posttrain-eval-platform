from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.repository import dlq as dlq_repo
from app.schemas import DLQList

router = APIRouter(prefix="/v1/dlq", tags=["dlq"])


@router.get("", response_model=DLQList)
def list_dlq(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = dlq_repo.list_(db, limit, offset)
    return DLQList(items=items, limit=limit, offset=offset, total=total)
