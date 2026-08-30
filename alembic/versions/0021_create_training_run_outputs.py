"""create training_run_outputs table

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_run_outputs",
        sa.Column("training_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), primary_key=True),
        sa.Column("final_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id"), nullable=False, unique=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("training_run_outputs")
