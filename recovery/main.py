import time

from app.config import settings
from app.db import SessionLocal
from app.logging_conf import logger
from app.services.recovery import run_once


def run() -> None:
    logger.info("recovery_started")
    while True:
        db = SessionLocal()
        try:
            reclaimed, dispatched = run_once(db)
            if reclaimed or dispatched:
                logger.info("recovery_cycle", extra={"reclaimed": reclaimed, "dispatched": dispatched})
        except Exception:
            logger.exception("recovery_error")
        finally:
            db.close()
        time.sleep(settings.recovery_poll_interval_ms / 1000)


if __name__ == "__main__":
    run()
