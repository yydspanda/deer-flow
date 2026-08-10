"""Add shadow tenant policy decisions.

Revision ID: 0022_tenant_policy_decisions
Revises: 0021_memory_pattern_observations
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_tenant_policy_decisions"
down_revision: str | Sequence[str] | None = "0021_memory_pattern_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_tenant_policy_decisions",
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("decision_key", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluation_status", sa.String(length=32), nullable=False),
        sa.Column("selected_rule_id", sa.String(length=128), nullable=True),
        sa.Column("detection_verdict", sa.String(length=32), nullable=False),
        sa.Column("recommended_disposition", sa.String(length=64), nullable=True),
        sa.Column("created_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint("decision_key"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in (
        "decision_key",
        "idempotency_key",
        "run_id",
        "alert_id",
        "tenant_id",
        "environment",
        "policy_id",
        "policy_version",
        "policy_hash",
        "policy_time",
        "evaluation_status",
        "selected_rule_id",
        "detection_verdict",
        "recommended_disposition",
        "created_by_actor_id",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_soc_tenant_policy_decisions_{column}"),
            "soc_tenant_policy_decisions",
            [column],
        )
    op.create_index(
        "ix_soc_tenant_policy_run_created",
        "soc_tenant_policy_decisions",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_soc_tenant_policy_alert_created",
        "soc_tenant_policy_decisions",
        ["alert_id", "created_at"],
    )
    op.create_index(
        "ix_soc_tenant_policy_scope_created",
        "soc_tenant_policy_decisions",
        ["tenant_id", "environment", "created_at"],
    )
    op.create_index(
        "ix_soc_tenant_policy_identity_created",
        "soc_tenant_policy_decisions",
        ["policy_id", "policy_version", "created_at"],
    )


def downgrade() -> None:
    for name in (
        "ix_soc_tenant_policy_identity_created",
        "ix_soc_tenant_policy_scope_created",
        "ix_soc_tenant_policy_alert_created",
        "ix_soc_tenant_policy_run_created",
    ):
        op.drop_index(name, table_name="soc_tenant_policy_decisions")
    for column in reversed(
        (
            "decision_key",
            "idempotency_key",
            "run_id",
            "alert_id",
            "tenant_id",
            "environment",
            "policy_id",
            "policy_version",
            "policy_hash",
            "policy_time",
            "evaluation_status",
            "selected_rule_id",
            "detection_verdict",
            "recommended_disposition",
            "created_by_actor_id",
            "created_at",
        )
    ):
        op.drop_index(
            op.f(f"ix_soc_tenant_policy_decisions_{column}"),
            table_name="soc_tenant_policy_decisions",
        )
    op.drop_table("soc_tenant_policy_decisions")
