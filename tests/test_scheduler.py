import threading

from sqlalchemy.orm import sessionmaker

from app.models.job import JobStatus
from app.repository import capacity as capacity_repo
from app.repository import jobs as repo
from app.repository import reservations as reservations_repo
from app.schemas import JobCreate
from app.services import jobs as service
from app.services.scheduler import rank_candidates, try_admit
from tests.conftest import engine, TEST_TOTAL_GPU


def _job_with_gpu(db, gpu, priority=50):
    return service.create_job(
        db, JobCreate(job_type="sft", priority=priority, config={"resources": {"gpu": gpu}})
    )


def test_admits_when_capacity_available(db_session):
    job = _job_with_gpu(db_session, gpu=1)

    admitted = try_admit(db_session, job)

    assert admitted is True
    cap = capacity_repo.get(db_session)
    assert cap.allocated_gpu == 1
    reservation = reservations_repo.get(db_session, job.id, attempt_number=1)
    assert reservation is not None
    assert reservation.status == "ACTIVE"
    assert reservation.gpu == 1


def test_waits_when_capacity_insufficient(db_session):
    job = _job_with_gpu(db_session, gpu=TEST_TOTAL_GPU + 1)

    admitted = try_admit(db_session, job)

    assert admitted is False
    cap = capacity_repo.get(db_session)
    assert cap.allocated_gpu == 0
    assert reservations_repo.get(db_session, job.id, attempt_number=1) is None


def test_exceeds_total_capacity_reason_is_distinct(db_session):
    from app.repository import scheduling_decisions as decisions_repo

    job = _job_with_gpu(db_session, gpu=TEST_TOTAL_GPU + 1)  # can never fit, ever

    try_admit(db_session, job)

    decisions = decisions_repo.list_for_job(db_session, job.id)
    assert decisions[-1].reason == "exceeds_total_cluster_capacity"


def test_insufficient_vs_exceeds_capacity_distinction(db_session):
    from app.repository import scheduling_decisions as decisions_repo

    # Consume all GPUs with one job, then a second job requesting 1 GPU
    # (which WOULD fit if capacity were free) gets "insufficient", not "exceeds".
    hog = _job_with_gpu(db_session, gpu=TEST_TOTAL_GPU)
    assert try_admit(db_session, hog) is True
    waiter = _job_with_gpu(db_session, gpu=1)

    admitted = try_admit(db_session, waiter)

    assert admitted is False
    decisions = decisions_repo.list_for_job(db_session, waiter.id)
    assert decisions[-1].reason == "insufficient_gpu_capacity"


def test_no_overcommit_under_concurrent_schedulers(db_session):
    """Release-blocking test, same tier as V0.3's split-brain test: N jobs
    together requesting more GPUs than exist, admitted concurrently by
    multiple scheduler 'processes' (threads with separate sessions) -- total
    allocated must never exceed total capacity."""
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    setup = TestSession()
    # 5 jobs x 2 GPU each = 10 requested, only TEST_TOTAL_GPU (8) available.
    job_ids = [_job_with_gpu(setup, gpu=2).id for _ in range(5)]
    setup.close()

    results = []

    def scheduler_pass(job_id):
        session = TestSession()
        try:
            job = repo.get(session, job_id)
            results.append(try_admit(session, job))
        finally:
            session.close()

    threads = [threading.Thread(target=scheduler_pass, args=(jid,)) for jid in job_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    verify = TestSession()
    cap = capacity_repo.get(verify)
    assert cap.allocated_gpu <= TEST_TOTAL_GPU
    assert cap.allocated_gpu == results.count(True) * 2
    # Ground-truth conservation invariant (DB_SCHEMA_CHANGES_V0.4.md).
    _, _, gpu_sum = reservations_repo.sum_active(verify)
    assert gpu_sum == cap.allocated_gpu
    verify.close()


def test_release_idempotency_and_conservation_invariant(db_session):
    """FAILURE_SCENARIOS_V0.4.md #7/#16: double-release must not double-decrement."""
    job = _job_with_gpu(db_session, gpu=2)
    try_admit(db_session, job)
    cap = capacity_repo.get(db_session)
    assert cap.allocated_gpu == 2

    first_release = reservations_repo.release(db_session, job.id, attempt_number=1)
    assert first_release is not None
    capacity_repo.release(db_session, first_release.cpu, first_release.memory_mb, first_release.gpu)
    db_session.commit()
    cap = capacity_repo.get(db_session)
    assert cap.allocated_gpu == 0

    second_release = reservations_repo.release(db_session, job.id, attempt_number=1)
    assert second_release is None  # no-op: already RELEASED
    # Caller correctly does NOT call capacity_repo.release again (worker.py's
    # pattern: only decrement inside the `if released is not None` branch).
    cap = capacity_repo.get(db_session)
    assert cap.allocated_gpu == 0  # NOT -2


def test_effective_priority_aging_lets_low_priority_eventually_rank_above_high_priority(db_session, monkeypatch):
    from app.config import settings
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text

    monkeypatch.setattr(settings, "aging_rate", 10.0)  # exaggerated for a fast, deterministic test
    monkeypatch.setattr(settings, "priority_ceiling", 100)

    low = service.create_job(db_session, JobCreate(job_type="sft", priority=10))
    high = service.create_job(db_session, JobCreate(job_type="sft", priority=90))
    # Make `low` look like it's been waiting a long time.
    db_session.execute(
        text("UPDATE jobs SET created_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=10), "id": str(low.id)},
    )
    db_session.commit()

    ranked = rank_candidates(db_session)
    ranked_ids_in_order = [job.id for _, job in ranked]

    assert ranked_ids_in_order.index(low.id) < ranked_ids_in_order.index(high.id)


def test_effective_priority_never_exceeds_ceiling(db_session, monkeypatch):
    from app.config import settings
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text

    monkeypatch.setattr(settings, "aging_rate", 1000.0)
    monkeypatch.setattr(settings, "priority_ceiling", 100)

    job = service.create_job(db_session, JobCreate(job_type="sft", priority=10))
    db_session.execute(
        text("UPDATE jobs SET created_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(hours=1), "id": str(job.id)},
    )
    db_session.commit()

    ranked = rank_candidates(db_session)
    effective_priority = ranked[0][0]

    assert effective_priority == settings.priority_ceiling  # capped, never exceeds
