"""Create shadow disposition evaluation artifacts.

Revision ID: 0016_disposition_evaluation
Revises: 0015_disposition_proposals
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_disposition_evaluation"
down_revision: str | Sequence[str] | None = "0015_disposition_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_disposition_sample_manifests",
        sa.Column("sample_id", sa.String(length=64), nullable=False),
        sa.Column("sample_key", sa.String(length=64), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("population_hash", sa.String(length=64), nullable=False),
        sa.Column("population_count", sa.Integer(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("sample_id"),
    )
    with op.batch_alter_table("soc_disposition_sample_manifests", schema=None) as batch_op:
        for column, unique in (
            ("created_at", False),
            ("created_by_actor_id", False),
            ("idempotency_key", True),
            ("population_hash", False),
            ("sample_key", True),
            ("scope_hash", False),
        ):
            batch_op.create_index(batch_op.f(f"ix_soc_disposition_sample_manifests_{column}"), [column], unique=unique)

    op.create_table(
        "soc_disposition_outcomes",
        sa.Column("outcome_id", sa.String(length=64), nullable=False),
        sa.Column("lineage_key", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("queue_id", sa.String(length=64), nullable=False),
        sa.Column("review_kind", sa.String(length=64), nullable=False),
        sa.Column("outcome_status", sa.String(length=32), nullable=False),
        sa.Column("observed_disposition", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("sample_id", sa.String(length=64), nullable=True),
        sa.Column("supersedes_outcome_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("reviewed_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("outcome_id"),
    )
    with op.batch_alter_table("soc_disposition_outcomes", schema=None) as batch_op:
        batch_op.create_index(
            "ix_soc_disposition_outcome_proposal_reviewed",
            ["proposal_id", "review_kind", "observed_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_soc_disposition_outcome_queue_reviewed",
            ["queue_id", "observed_at"],
            unique=False,
        )
        for column, unique in (
            ("alert_id", False),
            ("created_at", False),
            ("idempotency_key", True),
            ("lineage_key", True),
            ("observed_at", False),
            ("observed_disposition", False),
            ("outcome_status", False),
            ("proposal_id", False),
            ("queue_id", False),
            ("review_kind", False),
            ("reviewed_by_actor_id", False),
            ("run_id", False),
            ("sample_id", False),
            ("source", False),
            ("supersedes_outcome_id", False),
        ):
            batch_op.create_index(batch_op.f(f"ix_soc_disposition_outcomes_{column}"), [column], unique=unique)


def downgrade() -> None:
    op.drop_table("soc_disposition_outcomes")
    op.drop_table("soc_disposition_sample_manifests")
