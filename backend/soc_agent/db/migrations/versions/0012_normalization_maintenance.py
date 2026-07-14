"""Create normalization schema baseline and maintenance issue tables.

Revision ID: 0012_normalization_maintenance
Revises: 0011_memory_records
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_normalization_maintenance"
down_revision: str | Sequence[str] | None = "0011_memory_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_normalization_schema_baselines",
        sa.Column("baseline_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("source_system", sa.String(length=128), nullable=True),
        sa.Column("adapter", sa.String(length=128), nullable=False),
        sa.Column("parser_name", sa.String(length=128), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("accepted_fingerprints", sa.JSON(), nullable=False),
        sa.Column("approved_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("baseline_id"),
    )
    with op.batch_alter_table("soc_normalization_schema_baselines", schema=None) as batch_op:
        batch_op.create_index(
            "ix_soc_normalization_baseline_scope_status",
            ["tenant_id", "source_system", "adapter", "parser_name", "parser_version", "status"],
            unique=False,
        )
        for column in (
            "adapter",
            "approved_by_actor_id",
            "created_at",
            "parser_name",
            "parser_version",
            "source_system",
            "status",
            "superseded_at",
            "tenant_id",
            "updated_at",
        ):
            batch_op.create_index(batch_op.f(f"ix_soc_normalization_schema_baselines_{column}"), [column], unique=False)

    op.create_table(
        "soc_normalization_maintenance_issues",
        sa.Column("issue_id", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=256), nullable=False),
        sa.Column("issue_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("source_system", sa.String(length=128), nullable=True),
        sa.Column("adapter", sa.String(length=128), nullable=False),
        sa.Column("parser_name", sa.String(length=128), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("schema_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("source_path", sa.String(length=512), nullable=True),
        sa.Column("expected_target", sa.String(length=256), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("alert_id", sa.String(length=128), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_by_actor_id", sa.String(length=128), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_actor_id", sa.String(length=128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("issue_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("issue_id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    with op.batch_alter_table("soc_normalization_maintenance_issues", schema=None) as batch_op:
        batch_op.create_index("ix_soc_normalization_issue_status_seen", ["status", "last_seen_at"], unique=False)
        batch_op.create_index("ix_soc_normalization_issue_scope_status", ["tenant_id", "source_system", "status"], unique=False)
        for column, unique in (
            ("acknowledged_at", False),
            ("acknowledged_by_actor_id", False),
            ("adapter", False),
            ("alert_id", False),
            ("dedupe_key", True),
            ("expected_target", False),
            ("first_seen_at", False),
            ("issue_type", False),
            ("last_seen_at", False),
            ("parser_name", False),
            ("parser_version", False),
            ("resolved_at", False),
            ("resolved_by_actor_id", False),
            ("run_id", False),
            ("schema_fingerprint", False),
            ("severity", False),
            ("source_system", False),
            ("status", False),
            ("tenant_id", False),
        ):
            batch_op.create_index(
                batch_op.f(f"ix_soc_normalization_maintenance_issues_{column}"),
                [column],
                unique=unique,
            )


def downgrade() -> None:
    op.drop_table("soc_normalization_maintenance_issues")
    op.drop_table("soc_normalization_schema_baselines")
