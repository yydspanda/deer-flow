"""Create SOC alert summaries table.

Revision ID: 0003_alert_summaries
Revises: 0002_decision_audit_log
Create Date: 2026-06-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_alert_summaries"
down_revision: str | Sequence[str] | None = "0002_decision_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_alert_summaries",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=128), nullable=True),
        sa.Column("detection_key", sa.String(length=256), nullable=True),
        sa.Column("rule_code", sa.String(length=128), nullable=True),
        sa.Column("rule_name", sa.String(length=256), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("entity_keys", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("replay_of_run_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    with op.batch_alter_table("soc_alert_summaries", schema=None) as batch_op:
        batch_op.create_index("ix_soc_alert_summaries_alert_status", ["alert_id", "status"], unique=False)
        batch_op.create_index("ix_soc_alert_summaries_detection_updated", ["detection_key", "updated_at"], unique=False)
        batch_op.create_index("ix_soc_alert_summaries_review_updated", ["needs_review", "updated_at"], unique=False)
        batch_op.create_index("ix_soc_alert_summaries_source_updated", ["source_type", "updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_alert_id"), ["alert_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_category"), ["category"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_detection_key"), ["detection_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_input_hash"), ["input_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_needs_review"), ["needs_review"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_replay_of_run_id"), ["replay_of_run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_rule_code"), ["rule_code"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_rule_name"), ["rule_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_severity"), ["severity"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_source_system"), ["source_system"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_source_type"), ["source_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_updated_at"), ["updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_alert_summaries_verdict"), ["verdict"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_alert_summaries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_verdict"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_updated_at"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_status"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_source_type"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_source_system"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_severity"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_rule_name"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_rule_code"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_replay_of_run_id"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_needs_review"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_input_hash"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_detection_key"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_category"))
        batch_op.drop_index(batch_op.f("ix_soc_alert_summaries_alert_id"))
        batch_op.drop_index("ix_soc_alert_summaries_source_updated")
        batch_op.drop_index("ix_soc_alert_summaries_review_updated")
        batch_op.drop_index("ix_soc_alert_summaries_detection_updated")
        batch_op.drop_index("ix_soc_alert_summaries_alert_status")
    op.drop_table("soc_alert_summaries")
