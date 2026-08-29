"""create scheduling_decisions table

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduling_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("requested_cpu", sa.Integer(), nullable=False),
        sa.Column("requested_memory_mb", sa.Integer(), nullable=False),
        sa.Column("requested_gpu", sa.Integer(), nullable=False),
        sa.Column("available_cpu_snapshot", sa.Integer(), nullable=False),
        sa.Column("available_memory_mb_snapshot", sa.Integer(), nullable=False),
        sa.Column("available_gpu_snapshot", sa.Integer(), nullable=False),
        sa.Column("effective_priority", sa.Numeric(), nullable=False),
    )
    op.create_index("ix_scheduling_decisions_job_id", "scheduling_decisions", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_scheduling_decisions_job_id", table_name="scheduling_decisions")
    op.drop_table("scheduling_decisions")
