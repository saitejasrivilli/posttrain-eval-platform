"""create training_runs table

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_number", sa.Integer(), nullable=False),
        sa.Column("base_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_model_version_number", sa.Integer(), nullable=True),
        sa.Column("training_config", sa.JSON(), nullable=False),
        sa.Column("code_commit", sa.String(), nullable=False),
        sa.Column("container_image", sa.String(), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_training_runs_dataset_version",
        "training_runs",
        "dataset_versions",
        ["dataset_id", "dataset_version_number"],
        ["dataset_id", "version_number"],
    )
    op.create_foreign_key(
        "fk_training_runs_base_model_version",
        "training_runs",
        "model_versions",
        ["base_model_id", "base_model_version_number"],
        ["model_id", "version_number"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_training_runs_base_model_version", "training_runs", type_="foreignkey")
    op.drop_constraint("fk_training_runs_dataset_version", "training_runs", type_="foreignkey")
    op.drop_table("training_runs")
