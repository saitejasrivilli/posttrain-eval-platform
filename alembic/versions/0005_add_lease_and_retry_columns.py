"""add lease/attempt/retry columns to jobs

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("lease_owner", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "next_retry_at")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "lease_owner")
    op.drop_column("jobs", "attempt_number")
