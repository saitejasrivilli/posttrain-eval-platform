import json
import uuid

from app.config import settings
from app.db import SessionLocal
from app.kafka_client import make_consumer
from app.logging_conf import logger
from app.services.worker import process_job_message


def run() -> None:
    consumer = make_consumer(topic="job.queued", group_id="workers")
    logger.info("worker_started", extra={"worker_id": settings.worker_id})
    for message in consumer:
        job_id = uuid.UUID(json.loads(message.value)["job_id"])
        db = SessionLocal()
        try:
            outcome = process_job_message(db, job_id, settings.worker_id)
            logger.info(
                "worker_processed_message",
                extra={"job_id": str(job_id), "outcome": outcome, "worker_id": settings.worker_id},
            )
        except Exception:
            logger.exception("worker_processing_error", extra={"job_id": str(job_id)})
        finally:
            db.close()
        # Offset committed after handling regardless of claim outcome -- a
        # message for an already-terminal job is a legitimate no-op ack, not
        # an error (ADR 003). If process_job_message raised, the offset is
        # still committed here in V0.2 (no DLQ/retry engine yet -- that's
        # V0.3; an unhandled exception is a bug to fix, not a case to retry).
        consumer.commit()


if __name__ == "__main__":
    run()
