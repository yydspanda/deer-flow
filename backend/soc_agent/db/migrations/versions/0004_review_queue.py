"""Create SOC review queue table.

Revision ID: 0004_review_queue
Revises: 0003_alert_summaries
Create Date: 2026-06-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_review_queue"
down_revision: str | Sequence[str] | None = "0003_alert_summaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_review_queue",
        sa.Column("queue_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=128), nullable=True),
        sa.Column("rule_code", sa.String(length=128), nullable=True),
        sa.Column("rule_name", sa.String(length=256), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("entity_keys", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_payload", sa.JSON(), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("item_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("queue_id"),
    )
    with op.batch_alter_table("soc_review_queue", schema=None) as batch_op:
        batch_op.create_index("ix_soc_review_queue_alert_status", ["alert_id", "status"], unique=False)
        batch_op.create_index("ix_soc_review_queue_status_priority", ["status", "priority", "updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_alert_id"), ["alert_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_category"), ["category"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_closed_at"), ["closed_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_priority"), ["priority"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_reason"), ["reason"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_rule_code"), ["rule_code"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_rule_name"), ["rule_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_severity"), ["severity"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_source_system"), ["source_system"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_source_type"), ["source_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_updated_at"), ["updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_review_queue_verdict"), ["verdict"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_review_queue", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_verdict"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_updated_at"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_status"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_source_type"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_source_system"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_severity"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_run_id"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_rule_name"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_rule_code"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_reason"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_priority"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_closed_at"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_category"))
        batch_op.drop_index(batch_op.f("ix_soc_review_queue_alert_id"))
        batch_op.drop_index("ix_soc_review_queue_status_priority")
        batch_op.drop_index("ix_soc_review_queue_alert_status")
    op.drop_table("soc_review_queue")
