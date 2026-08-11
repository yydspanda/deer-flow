"""Add indexed tenant-policy and effective-decision stage lineage.

Revision ID: 0024_decision_stages
Revises: 0023_governed_automation
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_decision_stages"
down_revision: str | Sequence[str] | None = "0023_governed_automation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_tenant_policy_columns()
    _add_decision_stage_columns()


def downgrade() -> None:
    _drop_decision_stage_columns()
    _drop_tenant_policy_columns()


def _add_tenant_policy_columns() -> None:
    table = "soc_tenant_policy_decisions"
    columns = (
        sa.Column("policy_mode", sa.String(length=32), nullable=True),
        sa.Column("review_effect", sa.String(length=32), nullable=True),
        sa.Column("auto_apply_allowed", sa.Boolean(), nullable=True),
        sa.Column("disposition_impact", sa.String(length=32), nullable=True),
    )
    for column in columns:
        op.add_column(table, column)
        op.create_index(f"ix_{table}_{column.name}", table, [column.name])


def _add_decision_stage_columns() -> None:
    table = "soc_decision_transitions"
    columns = (
        sa.Column("memory_stage_status", sa.String(length=32), nullable=True),
        sa.Column("tenant_policy_stage_status", sa.String(length=32), nullable=True),
        sa.Column("tenant_policy_decision_id", sa.String(length=64), nullable=True),
        sa.Column("effective_disposition", sa.String(length=64), nullable=True),
    )
    for column in columns:
        op.add_column(table, column)
        op.create_index(f"ix_{table}_{column.name}", table, [column.name])


def _drop_decision_stage_columns() -> None:
    table = "soc_decision_transitions"
    for column in reversed(
        (
            "memory_stage_status",
            "tenant_policy_stage_status",
            "tenant_policy_decision_id",
            "effective_disposition",
        )
    ):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_column(table, column)


def _drop_tenant_policy_columns() -> None:
    table = "soc_tenant_policy_decisions"
    for column in reversed(
        (
            "policy_mode",
            "review_effect",
            "auto_apply_allowed",
            "disposition_impact",
        )
    ):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_column(table, column)
