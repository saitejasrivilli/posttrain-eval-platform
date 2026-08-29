from sqlalchemy.orm import Session

from app.repository import health as repo


def is_ready(db: Session) -> bool:
    try:
        repo.ping(db)
    except Exception:
        return False
    return True
