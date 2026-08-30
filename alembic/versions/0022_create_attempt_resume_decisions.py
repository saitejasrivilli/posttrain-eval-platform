"""create attempt_resume_decisions table

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attempt_resume_decisions",
        sa.Column("training_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), primary_key=True),
        sa.Column("attempt_number", sa.Integer(), primary_key=True),
        sa.Column("resumed_from_step", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("attempt_resume_decisions")
