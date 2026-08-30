"""create V0.7 evaluation + quality-gate tables

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-29

Adds the evaluation control plane (EVALUATION_MODEL_V0.7.md /
DB_SCHEMA_CHANGES_V0.7.md). No destructive change to any V0.1-V0.6 table --
all new tables. Candidate/baseline model references and dataset references
use the same fixed FK-chain lineage as V0.5/V0.6 (ADR 012/018).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("metric_definitions", postgresql.JSONB(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("max_examples", sa.Integer(), nullable=True),
        sa.Column("max_sequence_length", sa.Integer(), nullable=True),
        sa.Column("evaluator_code_commit", sa.String(), nullable=False),
        sa.Column("container_image", sa.String(), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_number", sa.Integer(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_number", sa.Integer(), nullable=False),
        sa.Column("evaluation_config_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_configs.id"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("baseline_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("baseline_model_version_number", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("evaluator_code_commit", sa.String(), nullable=False),
        sa.Column("container_image", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["model_id", "model_version_number"],
            ["model_versions.model_id", "model_versions.version_number"],
            name="fk_evaluation_runs_model_version",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "dataset_version_number"],
            ["dataset_versions.dataset_id", "dataset_versions.version_number"],
            name="fk_evaluation_runs_dataset_version",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_model_id", "baseline_model_version_number"],
            ["model_versions.model_id", "model_versions.version_number"],
            name="fk_evaluation_runs_baseline_model_version",
        ),
    )

    op.create_table(
        "evaluation_results",
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_runs.id"), primary_key=True),
        sa.Column("example_id", sa.String(), primary_key=True),
        sa.Column("prediction", sa.Text(), nullable=True),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "evaluation_metrics",
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_runs.id"), primary_key=True),
        sa.Column("metric_name", sa.String(), primary_key=True),
        sa.Column("split", sa.String(), primary_key=True),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "quality_gates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("rules", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "quality_gate_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_runs.id"), nullable=False),
        sa.Column("quality_gate_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("quality_gates.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("rule_results", postgresql.JSONB(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evaluation_run_id", "quality_gate_id",
                            name="uq_quality_gate_results_run_gate"),
    )


def downgrade() -> None:
    op.drop_table("quality_gate_results")
    op.drop_table("quality_gates")
    op.drop_table("evaluation_metrics")
    op.drop_table("evaluation_results")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_configs")
