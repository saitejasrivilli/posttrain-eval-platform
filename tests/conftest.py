import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
)

from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import job, outbox, attempt, dlq, capacity, reservation, scheduling_decision  # noqa: F401,E402
from app.models.capacity import SINGLETON_ID  # noqa: E402

engine = create_engine(os.environ["DATABASE_URL"])
TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Default test cluster capacity -- generous enough that resource-scarcity
# tests explicitly shrink it themselves rather than fighting a tiny default.
TEST_TOTAL_CPU = 64
TEST_TOTAL_MEMORY_MB = 262144
TEST_TOTAL_GPU = 8


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO capacity (id, total_cpu, allocated_cpu, total_memory_mb, "
                "allocated_memory_mb, total_gpu, allocated_gpu) "
                "VALUES (:id, :cpu, 0, :mem, 0, :gpu, 0) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(SINGLETON_ID), "cpu": TEST_TOTAL_CPU, "mem": TEST_TOTAL_MEMORY_MB, "gpu": TEST_TOTAL_GPU},
        )
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_table():
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE jobs, outbox, attempts, dlq, reservations, scheduling_decisions CASCADE"))
        conn.execute(
            text(
                "UPDATE capacity SET total_cpu=:cpu, allocated_cpu=0, total_memory_mb=:mem, "
                "allocated_memory_mb=0, total_gpu=:gpu, allocated_gpu=0 WHERE id=:id"
            ),
            {"cpu": TEST_TOTAL_CPU, "mem": TEST_TOTAL_MEMORY_MB, "gpu": TEST_TOTAL_GPU, "id": str(SINGLETON_ID)},
        )


def reserve_for_claim(db, job, cpu=1, memory_mb=512, gpu=0):
    """Test helper: V0.4 makes claim() require an ACTIVE reservation to
    exist for the attempt it's about to create. Pre-V0.4 tests that call
    repo.claim()/process_job_message() directly (bypassing the Scheduler)
    need this to admit the job first, mirroring what app/services/scheduler.py
    does in production."""
    from app.repository import capacity as capacity_repo
    from app.repository import reservations as reservations_repo

    prospective_attempt_number = job.attempt_number + 1
    reserved = capacity_repo.try_reserve(db, cpu, memory_mb, gpu)
    assert reserved, "test helper: capacity insufficient for reservation"
    reservations_repo.insert(db, job.id, prospective_attempt_number, cpu, memory_mb, gpu)
    db.commit()


@pytest.fixture()
def db_session():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
