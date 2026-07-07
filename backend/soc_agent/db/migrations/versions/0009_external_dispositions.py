"""Create SOC external disposition feedback table.

Revision ID: 0009_external_dispositions
Revises: 0008_investigation_evidence
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_external_dispositions"
down_revision: str | Sequence[str] | None = "0008_investigation_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_external_dispositions",
        sa.Column("disposition_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("external_system", sa.String(length=128), nullable=False),
        sa.Column("external_case_id", sa.String(length=256), nullable=False),
        sa.Column("source_event_id", sa.String(length=256), nullable=True),
        sa.Column("source_version", sa.String(length=256), nullable=True),
        sa.Column("external_status", sa.String(length=256), nullable=False),
        sa.Column("canonical_status", sa.String(length=64), nullable=False),
        sa.Column("apply_status", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("target_run_id", sa.String(length=64), nullable=True),
        sa.Column("target_alert_id", sa.String(length=128), nullable=True),
        sa.Column("target_queue_id", sa.String(length=64), nullable=True),
        sa.Column("matched_by", sa.String(length=64), nullable=True),
        sa.Column("audit_id", sa.String(length=64), nullable=True),
        sa.Column("correction_id", sa.String(length=64), nullable=True),
        sa.Column("memory_candidate_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disposition_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("disposition_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    with op.batch_alter_table("soc_external_dispositions", schema=None) as batch_op:
        batch_op.create_index("ix_soc_external_dispositions_alert_created", ["target_alert_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_external_dispositions_apply_created", ["apply_status", "created_at"], unique=False)
        batch_op.create_index("ix_soc_external_dispositions_case_created", ["external_system", "external_case_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_external_dispositions_queue_created", ["target_queue_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_external_dispositions_run_created", ["target_run_id", "created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_apply_status"), ["apply_status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_audit_id"), ["audit_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_canonical_status"), ["canonical_status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_correction_id"), ["correction_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_external_case_id"), ["external_case_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_external_status"), ["external_status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_external_system"), ["external_system"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_idempotency_key"), ["idempotency_key"], unique=True)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_matched_by"), ["matched_by"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_memory_candidate_id"), ["memory_candidate_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_source_event_id"), ["source_event_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_target_alert_id"), ["target_alert_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_target_queue_id"), ["target_queue_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_target_run_id"), ["target_run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_external_dispositions_tenant_id"), ["tenant_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_external_dispositions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_target_run_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_target_queue_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_target_alert_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_source_event_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_memory_candidate_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_matched_by"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_idempotency_key"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_external_system"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_external_status"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_external_case_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_created_at"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_correction_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_canonical_status"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_audit_id"))
        batch_op.drop_index(batch_op.f("ix_soc_external_dispositions_apply_status"))
        batch_op.drop_index("ix_soc_external_dispositions_run_created")
        batch_op.drop_index("ix_soc_external_dispositions_queue_created")
        batch_op.drop_index("ix_soc_external_dispositions_case_created")
        batch_op.drop_index("ix_soc_external_dispositions_apply_created")
        batch_op.drop_index("ix_soc_external_dispositions_alert_created")
    op.drop_table("soc_external_dispositions")
