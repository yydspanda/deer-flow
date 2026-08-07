"""Add PI-03F3 repeated-pattern source observations.

Revision ID: 0021_memory_pattern_observations
Revises: 0020_skill_improvement_backlog
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_memory_pattern_observations"
down_revision: str | Sequence[str] | None = "0020_skill_improvement_backlog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_memory_pattern_observations",
        sa.Column("observation_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("aggregation_key", sa.String(length=64), nullable=False),
        sa.Column("lineage_key", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("data_class", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=256), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("pattern_dimension", sa.String(length=32), nullable=False),
        sa.Column("pattern_value", sa.String(length=256), nullable=False),
        sa.Column("mocked", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("observation_id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint(
            "aggregation_key",
            "source_id",
            name="uq_soc_memory_pattern_aggregation_source",
        ),
    )
    for column in (
        "idempotency_key",
        "aggregation_key",
        "lineage_key",
        "content_hash",
        "tenant_id",
        "environment",
        "data_class",
        "source_type",
        "source_id",
        "run_id",
        "alert_id",
        "pattern_dimension",
        "pattern_value",
        "observed_at",
        "window_start",
        "window_end",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_soc_memory_pattern_observations_{column}"),
            "soc_memory_pattern_observations",
            [column],
        )
    op.create_index(
        "ix_soc_memory_pattern_scope_window",
        "soc_memory_pattern_observations",
        ["tenant_id", "environment", "data_class", "window_start"],
    )
    op.create_index(
        "ix_soc_memory_pattern_lineage_window",
        "soc_memory_pattern_observations",
        ["lineage_key", "window_start"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_soc_memory_pattern_lineage_window",
        table_name="soc_memory_pattern_observations",
    )
    op.drop_index(
        "ix_soc_memory_pattern_scope_window",
        table_name="soc_memory_pattern_observations",
    )
    for column in reversed(
        (
            "idempotency_key",
            "aggregation_key",
            "lineage_key",
            "content_hash",
            "tenant_id",
            "environment",
            "data_class",
            "source_type",
            "source_id",
            "run_id",
            "alert_id",
            "pattern_dimension",
            "pattern_value",
            "observed_at",
            "window_start",
            "window_end",
            "created_at",
        )
    ):
        op.drop_index(
            op.f(f"ix_soc_memory_pattern_observations_{column}"),
            table_name="soc_memory_pattern_observations",
        )
    op.drop_table("soc_memory_pattern_observations")
