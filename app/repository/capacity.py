from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.models.capacity import SINGLETON_ID, Capacity


def get(db: Session) -> Capacity | None:
    return db.query(Capacity).filter(Capacity.id == SINGLETON_ID).one_or_none()


def try_reserve(db: Session, cpu: int, memory_mb: int, gpu: int) -> bool:
    """Atomic capacity check-and-reserve, per ADR 007: the check IS the WHERE
    clause of a single UPDATE. Caller is responsible for the surrounding
    transaction (this does not commit -- see app/services/scheduler.py)."""
    stmt = (
        sa_update(Capacity)
        .where(
            Capacity.id == SINGLETON_ID,
            Capacity.allocated_cpu + cpu <= Capacity.total_cpu,
            Capacity.allocated_memory_mb + memory_mb <= Capacity.total_memory_mb,
            Capacity.allocated_gpu + gpu <= Capacity.total_gpu,
        )
        .values(
            allocated_cpu=Capacity.allocated_cpu + cpu,
            allocated_memory_mb=Capacity.allocated_memory_mb + memory_mb,
            allocated_gpu=Capacity.allocated_gpu + gpu,
        )
    )
    result = db.execute(stmt)
    return result.rowcount > 0


def release(db: Session, cpu: int, memory_mb: int, gpu: int) -> None:
    """Decrement allocated_*. Caller must only invoke this after winning the
    reservation's ACTIVE->RELEASED transition (see repository/reservations.py)
    -- this function itself has no idempotency check, by design: it is never
    called speculatively, only from inside the branch that already confirmed
    exactly one release is happening."""
    stmt = sa_update(Capacity).where(Capacity.id == SINGLETON_ID).values(
        allocated_cpu=Capacity.allocated_cpu - cpu,
        allocated_memory_mb=Capacity.allocated_memory_mb - memory_mb,
        allocated_gpu=Capacity.allocated_gpu - gpu,
    )
    db.execute(stmt)


def which_dimension_insufficient(cap: Capacity, cpu: int, memory_mb: int, gpu: int) -> str:
    """Diagnostic only (for scheduling_decisions.reason) -- not used for the
    actual admission decision, which is always the atomic UPDATE above.
    Checked in the documented order: cpu, memory, gpu (SCHEDULING_POLICY_V0.4.md)."""
    if cap.allocated_cpu + cpu > cap.total_cpu:
        return "exceeds_total_cluster_capacity" if cpu > cap.total_cpu else "insufficient_cpu_capacity"
    if cap.allocated_memory_mb + memory_mb > cap.total_memory_mb:
        return (
            "exceeds_total_cluster_capacity"
            if memory_mb > cap.total_memory_mb
            else "insufficient_memory_capacity"
        )
    if cap.allocated_gpu + gpu > cap.total_gpu:
        return "exceeds_total_cluster_capacity" if gpu > cap.total_gpu else "insufficient_gpu_capacity"
    return "resources_available"
