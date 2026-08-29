"""create dlq table

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dlq",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("moved_to_dlq_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_number", sa.Integer(), nullable=False),
        sa.Column("last_error_message", sa.String(), nullable=True),
        sa.Column("last_error_classification", sa.String(), nullable=False),
        sa.Column("total_attempts", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dlq")
