import time

from app.config import settings
from app.db import SessionLocal
from app.kafka_client import make_producer
from app.logging_conf import logger
from outbox_relay.relay import relay_once


def run() -> None:
    producer = make_producer()
    logger.info("outbox_relay_started")
    while True:
        db = SessionLocal()
        try:
            published = relay_once(db, producer)
            if published:
                logger.info("outbox_relay_cycle", extra={"published": published})
        except Exception:
            logger.exception("outbox_relay_error")
        finally:
            db.close()
        time.sleep(settings.outbox_poll_interval_ms / 1000)


if __name__ == "__main__":
    run()
