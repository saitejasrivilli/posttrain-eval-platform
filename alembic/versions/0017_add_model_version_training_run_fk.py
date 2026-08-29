"""add FK constraint model_versions.training_run_id -> training_runs.id

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-29

"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_model_versions_training_run",
        "model_versions",
        "training_runs",
        ["training_run_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_model_versions_training_run", "model_versions", type_="foreignkey")
