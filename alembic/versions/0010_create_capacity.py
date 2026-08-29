"""create capacity table, seed the singleton row

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SINGLETON_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "capacity",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("total_cpu", sa.Integer(), nullable=False),
        sa.Column("allocated_cpu", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_memory_mb", sa.Integer(), nullable=False),
        sa.Column("allocated_memory_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_gpu", sa.Integer(), nullable=False),
        sa.Column("allocated_gpu", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Seed with a documented, operator-configurable default -- not invented
    # as "the" cluster size (RESOURCE_MODEL_V0.4.md). Revisit at deploy time.
    op.execute(
        f"""
        INSERT INTO capacity (id, total_cpu, total_memory_mb, total_gpu)
        VALUES ('{SINGLETON_ID}', 16, 65536, 4)
        """
    )


def downgrade() -> None:
    op.drop_table("capacity")
