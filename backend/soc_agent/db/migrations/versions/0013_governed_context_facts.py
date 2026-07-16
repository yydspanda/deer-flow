"""Create append-only governed context fact versions.

Revision ID: 0013_governed_context_facts
Revises: 0012_normalization_maintenance
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_governed_context_facts"
down_revision: str | Sequence[str] | None = "0012_normalization_maintenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_governed_context_facts",
        sa.Column("fact_version_id", sa.String(length=64), nullable=False),
        sa.Column("fact_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("current_key", sa.String(length=64), nullable=True),
        sa.Column("is_latest", sa.Boolean(), nullable=False),
        sa.Column("fact_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=False),
        sa.Column("source_version", sa.String(length=256), nullable=True),
        sa.Column("source_fresh_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("changed_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("reviewed_by_actor_id", sa.String(length=128), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_version_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fact_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("fact_version_id"),
        sa.UniqueConstraint("fact_id", "version", name="uq_soc_governed_context_fact_version"),
    )
    with op.batch_alter_table("soc_governed_context_facts", schema=None) as batch_op:
        batch_op.create_index(
            "ix_soc_governed_context_scope_status",
            ["tenant_id", "environment", "fact_type", "status", "is_latest"],
            unique=False,
        )
        batch_op.create_index(
            "ix_soc_governed_context_validity",
            ["tenant_id", "environment", "valid_from", "valid_until"],
            unique=False,
        )
        for column, unique in (
            ("changed_by_actor_id", False),
            ("content_hash", False),
            ("created_at", False),
            ("current_key", True),
            ("environment", False),
            ("fact_id", False),
            ("fact_type", False),
            ("is_latest", False),
            ("owner_id", False),
            ("reviewed_by_actor_id", False),
            ("source_fresh_until", False),
            ("source_type", False),
            ("state_changed_at", False),
            ("status", False),
            ("supersedes_version_id", False),
            ("tenant_id", False),
            ("updated_at", False),
            ("valid_from", False),
            ("valid_until", False),
        ):
            batch_op.create_index(
                batch_op.f(f"ix_soc_governed_context_facts_{column}"),
                [column],
                unique=unique,
            )


def downgrade() -> None:
    op.drop_table("soc_governed_context_facts")
