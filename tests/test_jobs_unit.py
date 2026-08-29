import uuid

import pytest
from fastapi import HTTPException

from app.models.job import Job, JobStatus
from app.schemas import JobCreate
from app.services import jobs as service


def test_create_job_defaults_to_pending(monkeypatch):
    captured = {}

    def fake_insert(db, job):
        captured["job"] = job
        return job

    monkeypatch.setattr(service.repo, "insert", fake_insert)

    result = service.create_job(db=None, payload=JobCreate(job_type="sft"))

    assert result.status == JobStatus.PENDING.value
    assert captured["job"].job_type == "sft"


def test_get_job_raises_404_when_missing(monkeypatch):
    monkeypatch.setattr(service.repo, "get", lambda db, job_id: None)

    with pytest.raises(HTTPException) as exc_info:
        service.get_job(db=None, job_id=uuid.uuid4())

    assert exc_info.value.status_code == 404


def test_list_jobs_rejects_limit_out_of_range():
    with pytest.raises(HTTPException) as exc_info:
        service.list_jobs(db=None, limit=0, offset=0)
    assert exc_info.value.status_code == 422

    with pytest.raises(HTTPException) as exc_info:
        service.list_jobs(db=None, limit=201, offset=0)
    assert exc_info.value.status_code == 422


def test_list_jobs_rejects_negative_offset():
    with pytest.raises(HTTPException) as exc_info:
        service.list_jobs(db=None, limit=20, offset=-1)
    assert exc_info.value.status_code == 422


def test_list_jobs_empty_page(monkeypatch):
    monkeypatch.setattr(service.repo, "list_", lambda db, limit, offset: ([], 0))

    items, total = service.list_jobs(db=None, limit=20, offset=0)

    assert items == []
    assert total == 0


def test_list_jobs_last_page(monkeypatch):
    fake_job = Job(id=uuid.uuid4(), job_type="sft", status=JobStatus.PENDING.value)
    monkeypatch.setattr(service.repo, "list_", lambda db, limit, offset: ([fake_job], 21))

    items, total = service.list_jobs(db=None, limit=20, offset=20)

    assert len(items) == 1
    assert total == 21


def test_is_ready_true_when_ping_succeeds(monkeypatch):
    from app.services import health as health_service

    monkeypatch.setattr(health_service.repo, "ping", lambda db: None)

    assert health_service.is_ready(db=None) is True


def test_is_ready_false_when_ping_raises(monkeypatch):
    from app.services import health as health_service

    def broken_ping(db):
        raise Exception("db down")

    monkeypatch.setattr(health_service.repo, "ping", broken_ping)

    assert health_service.is_ready(db=None) is False
