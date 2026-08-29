"""add priority to jobs

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="50"))


def downgrade() -> None:
    op.drop_column("jobs", "priority")
