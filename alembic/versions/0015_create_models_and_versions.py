"""create models and model_versions tables

model_versions.training_run_id has no FK constraint yet -- training_runs
doesn't exist until migration 0016 (circular reference: training_runs also
references model_versions for base_model). The FK constraint is added in
0017 once both tables exist.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_versions",
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("models.id"), primary_key=True),
        sa.Column("version_number", sa.Integer(), primary_key=True),
        sa.Column(
            "artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id"), nullable=False, unique=True
        ),
        sa.Column("training_run_id", postgresql.UUID(as_uuid=True), nullable=True),  # FK added in 0017
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("model_versions")
    op.drop_table("models")
