"""Create SOC memory candidate table.

Revision ID: 0010_memory_candidates
Revises: 0009_external_dispositions
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_memory_candidates"
down_revision: str | Sequence[str] | None = "0009_external_dispositions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_memory_candidates",
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_type", sa.String(length=64), nullable=False),
        sa.Column("target_artifact", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("tenant_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_surface", sa.String(length=32), nullable=True),
        sa.Column("source_id", sa.String(length=256), nullable=True),
        sa.Column("source_doc", sa.String(length=256), nullable=True),
        sa.Column("source_section", sa.String(length=256), nullable=True),
        sa.Column("capability_card_id", sa.String(length=128), nullable=True),
        sa.Column("source_run_id", sa.String(length=64), nullable=True),
        sa.Column("source_alert_id", sa.String(length=128), nullable=True),
        sa.Column("source_queue_id", sa.String(length=64), nullable=True),
        sa.Column("correction_id", sa.String(length=64), nullable=True),
        sa.Column("eval_sample_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("decision_impact", sa.String(length=64), nullable=False),
        sa.Column("runtime_decision_allowed", sa.Boolean(), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("review_owner", sa.String(length=128), nullable=True),
        sa.Column("reviewed_by_actor_id", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candidate_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("candidate_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    with op.batch_alter_table("soc_memory_candidates", schema=None) as batch_op:
        batch_op.create_index("ix_soc_memory_candidates_alert_created", ["source_alert_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_memory_candidates_queue_created", ["source_queue_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_memory_candidates_run_created", ["source_run_id", "created_at"], unique=False)
        batch_op.create_index("ix_soc_memory_candidates_source_created", ["source_type", "created_at"], unique=False)
        batch_op.create_index("ix_soc_memory_candidates_status_created", ["status", "created_at"], unique=False)
        batch_op.create_index("ix_soc_memory_candidates_tenant_status", ["tenant_scope", "tenant_id", "status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_candidate_type"), ["candidate_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_capability_card_id"), ["capability_card_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_correction_id"), ["correction_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_decision_impact"), ["decision_impact"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_eval_sample_id"), ["eval_sample_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_idempotency_key"), ["idempotency_key"], unique=True)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_review_owner"), ["review_owner"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_reviewed_at"), ["reviewed_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_reviewed_by_actor_id"), ["reviewed_by_actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_source_alert_id"), ["source_alert_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_source_doc"), ["source_doc"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_source_id"), ["source_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_source_queue_id"), ["source_queue_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_source_run_id"), ["source_run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_source_surface"), ["source_surface"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_source_type"), ["source_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_target_artifact"), ["target_artifact"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_tenant_scope"), ["tenant_scope"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_candidates_updated_at"), ["updated_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_memory_candidates", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_updated_at"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_tenant_scope"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_target_artifact"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_status"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_source_type"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_source_surface"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_source_run_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_source_queue_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_source_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_source_doc"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_source_alert_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_reviewed_by_actor_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_reviewed_at"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_review_owner"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_idempotency_key"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_eval_sample_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_decision_impact"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_created_at"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_correction_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_capability_card_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_candidates_candidate_type"))
        batch_op.drop_index("ix_soc_memory_candidates_tenant_status")
        batch_op.drop_index("ix_soc_memory_candidates_status_created")
        batch_op.drop_index("ix_soc_memory_candidates_source_created")
        batch_op.drop_index("ix_soc_memory_candidates_run_created")
        batch_op.drop_index("ix_soc_memory_candidates_queue_created")
        batch_op.drop_index("ix_soc_memory_candidates_alert_created")
    op.drop_table("soc_memory_candidates")
