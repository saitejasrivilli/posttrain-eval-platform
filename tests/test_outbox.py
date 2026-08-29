from unittest.mock import MagicMock

from app.models.job import JobStatus
from app.repository import outbox as outbox_repo
from app.schemas import JobCreate
from app.services import jobs as service
from outbox_relay.relay import relay_once


def test_create_job_writes_job_and_outbox_atomically(db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))

    unpublished = outbox_repo.list_unpublished(db_session)
    matching = [row for row in unpublished if row.job_id == job.id]
    assert len(matching) == 1
    assert matching[0].event_type == "job.queued"
    assert matching[0].payload["job_id"] == str(job.id)
    assert job.status == JobStatus.QUEUED.value


def test_relay_publishes_and_marks_published(db_session):
    service.create_job(db_session, JobCreate(job_type="sft"))
    fake_future = MagicMock()
    fake_future.get.return_value = None
    fake_producer = MagicMock()
    fake_producer.send.return_value = fake_future

    published_count = relay_once(db_session, fake_producer)

    assert published_count == 1
    assert fake_producer.send.called
    assert outbox_repo.list_unpublished(db_session) == []


def test_relay_leaves_row_unpublished_if_kafka_send_fails(db_session):
    service.create_job(db_session, JobCreate(job_type="sft"))
    fake_producer = MagicMock()
    fake_producer.send.side_effect = Exception("kafka unavailable")

    try:
        relay_once(db_session, fake_producer)
    except Exception:
        pass

    # Row must remain durable and unpublished -- no data loss (ADR 002,
    # failure scenario 1 / 7: DB commit succeeds, Kafka publish fails/unavailable).
    assert len(outbox_repo.list_unpublished(db_session)) == 1
