"""create training_metrics table

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("training_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("loss", sa.Float(), nullable=True),
        sa.Column("learning_rate", sa.Float(), nullable=True),
        sa.Column("gpu_memory_allocated_mb", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_training_metrics_run", "training_metrics", ["training_run_id"])


def downgrade() -> None:
    op.drop_index("ix_training_metrics_run", table_name="training_metrics")
    op.drop_table("training_metrics")
