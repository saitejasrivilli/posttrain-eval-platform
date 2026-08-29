from sqlalchemy import text
from sqlalchemy.orm import Session


def ping(db: Session) -> None:
    db.execute(text("SELECT 1"))
