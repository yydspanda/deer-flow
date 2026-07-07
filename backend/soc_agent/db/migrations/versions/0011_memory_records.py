"""Create SOC confirmed memory record table.

Revision ID: 0011_memory_records
Revises: 0010_memory_candidates
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_memory_records"
down_revision: str | Sequence[str] | None = "0010_memory_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_memory_records",
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("memory_type", sa.String(length=64), nullable=False),
        sa.Column("target_artifact", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("tenant_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("source_candidate_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), nullable=True),
        sa.Column("source_alert_id", sa.String(length=128), nullable=True),
        sa.Column("source_queue_id", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("facets_hash", sa.String(length=128), nullable=False),
        sa.Column("retrieval_enabled", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("deprecated_by_actor_id", sa.String(length=128), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("memory_id"),
        sa.UniqueConstraint("source_candidate_id"),
    )
    with op.batch_alter_table("soc_memory_records", schema=None) as batch_op:
        batch_op.create_index("ix_soc_memory_records_status_updated", ["status", "updated_at"], unique=False)
        batch_op.create_index("ix_soc_memory_records_tenant_status", ["tenant_scope", "tenant_id", "status"], unique=False)
        batch_op.create_index("ix_soc_memory_records_type_status", ["memory_type", "status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_content_hash"), ["content_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_created_by_actor_id"), ["created_by_actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_deprecated_at"), ["deprecated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_deprecated_by_actor_id"), ["deprecated_by_actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_facets_hash"), ["facets_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_memory_type"), ["memory_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_retrieval_enabled"), ["retrieval_enabled"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_source_alert_id"), ["source_alert_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_source_candidate_id"), ["source_candidate_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_source_queue_id"), ["source_queue_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_source_run_id"), ["source_run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_source_type"), ["source_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_target_artifact"), ["target_artifact"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_tenant_scope"), ["tenant_scope"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_memory_records_updated_at"), ["updated_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_memory_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_updated_at"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_tenant_scope"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_target_artifact"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_status"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_source_type"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_source_run_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_source_queue_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_source_candidate_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_source_alert_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_retrieval_enabled"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_memory_type"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_facets_hash"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_deprecated_by_actor_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_deprecated_at"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_created_by_actor_id"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_created_at"))
        batch_op.drop_index(batch_op.f("ix_soc_memory_records_content_hash"))
        batch_op.drop_index("ix_soc_memory_records_type_status")
        batch_op.drop_index("ix_soc_memory_records_tenant_status")
        batch_op.drop_index("ix_soc_memory_records_status_updated")
    op.drop_table("soc_memory_records")
