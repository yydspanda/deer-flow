"""Add persistent read-only investigation executions and action attempts.

Revision ID: 0019_enrichment_executions
Revises: 0018_mutation_audit
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_enrichment_executions"
down_revision: str | Sequence[str] | None = "0018_mutation_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_enrichment_executions",
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.String(length=256), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("replay_of_execution_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("execution_id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_soc_enrichment_execution_idempotency",
        ),
    )
    for column in (
        "trigger",
        "run_id",
        "alert_id",
        "thread_id",
        "plan_id",
        "status",
        "retryable",
        "replay_of_execution_id",
        "created_at",
        "updated_at",
        "completed_at",
    ):
        op.create_index(
            op.f(f"ix_soc_enrichment_executions_{column}"),
            "soc_enrichment_executions",
            [column],
        )
    op.create_index(
        "ix_soc_enrichment_execution_run_created",
        "soc_enrichment_executions",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_soc_enrichment_execution_status_updated",
        "soc_enrichment_executions",
        ["status", "updated_at"],
    )

    op.create_table(
        "soc_enrichment_action_attempts",
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("plan_action_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("action_idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("route", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("adapter_id", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint(
            "execution_id",
            "plan_action_id",
            "attempt_number",
            name="uq_soc_enrichment_attempt_identity",
        ),
        sa.UniqueConstraint(
            "action_idempotency_key",
            name="uq_soc_enrichment_attempt_idempotency",
        ),
    )
    for column in (
        "execution_id",
        "plan_action_id",
        "route",
        "action",
        "adapter_id",
        "status",
        "retryable",
        "evidence_id",
        "started_at",
        "ended_at",
    ):
        op.create_index(
            op.f(f"ix_soc_enrichment_action_attempts_{column}"),
            "soc_enrichment_action_attempts",
            [column],
        )
    op.create_index(
        "ix_soc_enrichment_attempt_execution_action",
        "soc_enrichment_action_attempts",
        ["execution_id", "plan_action_id", "attempt_number"],
    )
    op.create_index(
        "ix_soc_enrichment_attempt_status_started",
        "soc_enrichment_action_attempts",
        ["status", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_soc_enrichment_attempt_status_started",
        table_name="soc_enrichment_action_attempts",
    )
    op.drop_index(
        "ix_soc_enrichment_attempt_execution_action",
        table_name="soc_enrichment_action_attempts",
    )
    for column in reversed(
        (
            "execution_id",
            "plan_action_id",
            "route",
            "action",
            "adapter_id",
            "status",
            "retryable",
            "evidence_id",
            "started_at",
            "ended_at",
        )
    ):
        op.drop_index(
            op.f(f"ix_soc_enrichment_action_attempts_{column}"),
            table_name="soc_enrichment_action_attempts",
        )
    op.drop_table("soc_enrichment_action_attempts")

    op.drop_index(
        "ix_soc_enrichment_execution_status_updated",
        table_name="soc_enrichment_executions",
    )
    op.drop_index(
        "ix_soc_enrichment_execution_run_created",
        table_name="soc_enrichment_executions",
    )
    for column in reversed(
        (
            "trigger",
            "run_id",
            "alert_id",
            "thread_id",
            "plan_id",
            "status",
            "retryable",
            "replay_of_execution_id",
            "created_at",
            "updated_at",
            "completed_at",
        )
    ):
        op.drop_index(
            op.f(f"ix_soc_enrichment_executions_{column}"),
            table_name="soc_enrichment_executions",
        )
    op.drop_table("soc_enrichment_executions")
