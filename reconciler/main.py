import time

from app.config import settings
from app.db import SessionLocal
from app.logging_conf import logger
from app.services.reconciler import run_once
from app.storage import ensure_bucket, make_client


def run() -> None:
    client = make_client()
    ensure_bucket(client)
    logger.info("reconciler_started")
    while True:
        db = SessionLocal()
        try:
            healed, failed = run_once(db, client)
            if healed or failed:
                logger.info("reconciler_cycle", extra={"healed": healed, "failed": failed})
        except Exception:
            logger.exception("reconciler_error")
        finally:
            db.close()
        time.sleep(settings.reconciler_poll_interval_ms / 1000)


if __name__ == "__main__":
    run()
