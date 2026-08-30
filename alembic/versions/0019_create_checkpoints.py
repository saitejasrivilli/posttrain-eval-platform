"""create checkpoints table

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkpoints",
        sa.Column("training_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), primary_key=True),
        sa.Column("attempt_number", sa.Integer(), primary_key=True),
        sa.Column("step", sa.Integer(), primary_key=True),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id"), nullable=False, unique=True),
        sa.Column("base_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_model_version_number", sa.Integer(), nullable=True),
        sa.Column("checkpoint_format_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("checkpoints")
