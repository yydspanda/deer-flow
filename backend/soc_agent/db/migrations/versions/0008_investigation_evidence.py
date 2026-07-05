"""Create SOC investigation evidence table.

Revision ID: 0008_investigation_evidence
Revises: 0007_audit_idempotency_key
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_investigation_evidence"
down_revision: str | Sequence[str] | None = "0007_audit_idempotency_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_investigation_evidence",
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("route", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("queue_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("alert_id", sa.String(length=128), nullable=True),
        sa.Column("thread_id", sa.String(length=128), nullable=True),
        sa.Column("source_proposal_id", sa.String(length=64), nullable=True),
        sa.Column("context_hash", sa.String(length=128), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    with op.batch_alter_table("soc_investigation_evidence", schema=None) as batch_op:
        batch_op.create_index("ix_soc_investigation_evidence_action_created", ["action", "created_at"], unique=False)
        batch_op.create_index("ix_soc_investigation_evidence_alert_created", ["alert_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_investigation_evidence_queue_created", ["queue_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_investigation_evidence_run_created", ["run_id", "created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_actor_id"), ["actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_alert_id"), ["alert_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_context_hash"), ["context_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_queue_id"), ["queue_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_route"), ["route"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_source_proposal_id"), ["source_proposal_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_source_type"), ["source_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_investigation_evidence_thread_id"), ["thread_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_investigation_evidence", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_thread_id"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_status"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_source_type"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_source_proposal_id"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_run_id"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_route"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_queue_id"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_created_at"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_context_hash"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_alert_id"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_actor_id"))
        batch_op.drop_index(batch_op.f("ix_soc_investigation_evidence_action"))
        batch_op.drop_index("ix_soc_investigation_evidence_run_created")
        batch_op.drop_index("ix_soc_investigation_evidence_queue_created")
        batch_op.drop_index("ix_soc_investigation_evidence_alert_created")
        batch_op.drop_index("ix_soc_investigation_evidence_action_created")
    op.drop_table("soc_investigation_evidence")
