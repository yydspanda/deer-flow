"""Create append-only authorization enrichment records.

Revision ID: 0014_authorization_enrichments
Revises: 0013_governed_context_facts
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_authorization_enrichments"
down_revision: str | Sequence[str] | None = "0013_governed_context_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_authorization_enrichments",
        sa.Column("enrichment_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("queue_id", sa.String(length=64), nullable=True),
        sa.Column("match_status", sa.String(length=32), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("matcher_policy_version", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("replay_of_enrichment_id", sa.String(length=64), nullable=True),
        sa.Column("created_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enrichment_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("enrichment_id"),
    )
    with op.batch_alter_table("soc_authorization_enrichments", schema=None) as batch_op:
        batch_op.create_index(
            "ix_soc_authorization_enrichment_run_created",
            ["run_id", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_soc_authorization_enrichment_alert_created",
            ["alert_id", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_soc_authorization_enrichment_queue_created",
            ["queue_id", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_soc_authorization_enrichment_replay_created",
            ["replay_of_enrichment_id", "created_at"],
            unique=False,
        )
        for column, unique in (
            ("alert_id", False),
            ("created_at", False),
            ("created_by_actor_id", False),
            ("idempotency_key", True),
            ("match_status", False),
            ("matcher_policy_version", False),
            ("query_hash", False),
            ("queue_id", False),
            ("replay_of_enrichment_id", False),
            ("run_id", False),
        ):
            batch_op.create_index(
                batch_op.f(f"ix_soc_authorization_enrichments_{column}"),
                [column],
                unique=unique,
            )


def downgrade() -> None:
    op.drop_table("soc_authorization_enrichments")
