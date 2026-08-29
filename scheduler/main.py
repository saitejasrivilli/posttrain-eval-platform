import time

from app.config import settings
from app.db import SessionLocal
from app.logging_conf import logger
from app.services.scheduler import run_once


def run() -> None:
    logger.info("scheduler_started")
    while True:
        db = SessionLocal()
        try:
            admitted, waiting = run_once(db)
            if admitted or waiting:
                logger.info("scheduler_cycle", extra={"admitted": admitted, "waiting": waiting})
        except Exception:
            logger.exception("scheduler_error")
        finally:
            db.close()
        time.sleep(settings.scheduler_poll_interval_ms / 1000)


if __name__ == "__main__":
    run()
