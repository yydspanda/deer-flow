"""Create append-only shadow disposition proposals.

Revision ID: 0015_disposition_proposals
Revises: 0014_authorization_enrichments
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_disposition_proposals"
down_revision: str | Sequence[str] | None = "0014_authorization_enrichments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_disposition_proposals",
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("proposal_key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("queue_id", sa.String(length=64), nullable=False),
        sa.Column("source_enrichment_id", sa.String(length=64), nullable=False),
        sa.Column("proposed_disposition", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("detection_verdict", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("proposal_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    with op.batch_alter_table("soc_disposition_proposals", schema=None) as batch_op:
        batch_op.create_index("ix_soc_disposition_proposal_run_created", ["run_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_disposition_proposal_alert_created", ["alert_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_disposition_proposal_queue_created", ["queue_id", "created_at"], unique=False)
        batch_op.create_index(
            "ix_soc_disposition_proposal_enrichment_created",
            ["source_enrichment_id", "created_at"],
            unique=False,
        )
        for column, unique in (
            ("alert_id", False),
            ("created_at", False),
            ("created_by_actor_id", False),
            ("detection_verdict", False),
            ("idempotency_key", True),
            ("policy_version", False),
            ("proposal_key", True),
            ("proposed_disposition", False),
            ("queue_id", False),
            ("reason_code", False),
            ("run_id", False),
            ("source_enrichment_id", False),
        ):
            batch_op.create_index(
                batch_op.f(f"ix_soc_disposition_proposals_{column}"),
                [column],
                unique=unique,
            )


def downgrade() -> None:
    op.drop_table("soc_disposition_proposals")
