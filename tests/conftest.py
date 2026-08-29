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
from app.models import job, outbox, attempt, dlq  # noqa: F401,E402

engine = create_engine(os.environ["DATABASE_URL"])
TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_table():
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE jobs, outbox, attempts, dlq CASCADE"))


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
