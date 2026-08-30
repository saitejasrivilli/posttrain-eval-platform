"""add failure_domain to attempts

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attempts", sa.Column("failure_domain", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("attempts", "failure_domain")
