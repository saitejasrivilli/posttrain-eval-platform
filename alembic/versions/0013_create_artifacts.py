"""create artifacts table

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("content_hash", sa.String(), nullable=False, unique=True),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("artifact_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("uploader_id", sa.String(), nullable=True),
        sa.Column("upload_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_artifacts_pending", "artifacts", ["status"], postgresql_where=sa.text("status = 'PENDING'")
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_pending", table_name="artifacts")
    op.drop_table("artifacts")
