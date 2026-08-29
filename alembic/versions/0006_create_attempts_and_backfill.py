"""create attempts table and backfill from executions

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attempts",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("attempt_number", sa.Integer(), primary_key=True),
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("error_classification", sa.String(), nullable=True),
    )
    # Backfill: existing V0.2 executions become attempt_number=1 (ADR 006).
    op.execute(
        """
        INSERT INTO attempts (job_id, attempt_number, worker_id, status, started_at, finished_at)
        SELECT job_id, 1, worker_id, COALESCE(outcome, 'SUCCEEDED'), started_at, finished_at
        FROM executions
        """
    )
    op.execute("UPDATE jobs SET attempt_number = 1 WHERE attempt_number = 0 AND status != 'QUEUED'")


def downgrade() -> None:
    op.drop_table("attempts")
