import json
import logging

from sqlalchemy.orm import Session

from app.repository import outbox as outbox_repo

logger = logging.getLogger("app")


def relay_once(db: Session, producer) -> int:
    """Publish all currently-unpublished outbox rows. Returns count published.
    See ADR 002: this function is itself at-least-once -- if the process
    crashes between producer.send()'s ack and mark_published's commit, the
    row remains unpublished and republishes on the next run. Consumers must
    tolerate that duplicate (ADR 003)."""
    rows = outbox_repo.list_unpublished(db)
    published = 0
    for row in rows:
        producer.send(row.event_type, value=json.dumps(row.payload)).get(timeout=5)
        outbox_repo.mark_published(db, row.id)
        published += 1
        logger.info(
            "outbox_published",
            extra={"job_id": row.payload.get("job_id"), "event_type": row.event_type},
        )
    return published
