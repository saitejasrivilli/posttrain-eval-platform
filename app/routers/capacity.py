from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.repository import capacity as capacity_repo
from app.schemas import CapacityOut

router = APIRouter(prefix="/v1/capacity", tags=["capacity"])


@router.get("", response_model=CapacityOut)
def get_capacity(db: Session = Depends(get_db)):
    cap = capacity_repo.get(db)
    return CapacityOut(
        total_cpu=cap.total_cpu,
        allocated_cpu=cap.allocated_cpu,
        available_cpu=cap.total_cpu - cap.allocated_cpu,
        total_memory_mb=cap.total_memory_mb,
        allocated_memory_mb=cap.allocated_memory_mb,
        available_memory_mb=cap.total_memory_mb - cap.allocated_memory_mb,
        total_gpu=cap.total_gpu,
        allocated_gpu=cap.allocated_gpu,
        available_gpu=cap.total_gpu - cap.allocated_gpu,
    )
