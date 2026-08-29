"""create datasets and dataset_versions tables

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "dataset_versions",
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), primary_key=True),
        sa.Column("version_number", sa.Integer(), primary_key=True),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
