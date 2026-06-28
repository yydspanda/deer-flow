"""Create SOC decision audit log table.

Revision ID: 0002_decision_audit_log
Revises: 0001_soc_analysis_runs
Create Date: 2026-06-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_decision_audit_log"
down_revision: str | Sequence[str] | None = "0001_soc_analysis_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_decision_audit_log",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_surface", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("previous_verdict", sa.String(length=32), nullable=True),
        sa.Column("final_verdict", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("replay_of_run_id", sa.String(length=64), nullable=True),
        sa.Column("correction_id", sa.String(length=64), nullable=True),
        sa.Column("record_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    with op.batch_alter_table("soc_decision_audit_log", schema=None) as batch_op:
        batch_op.create_index("ix_soc_decision_audit_alert_action", ["alert_id", "action"], unique=False)
        batch_op.create_index("ix_soc_decision_audit_run_action", ["run_id", "action"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_alert_id"), ["alert_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_correction_id"), ["correction_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_final_verdict"), ["final_verdict"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_input_hash"), ["input_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_occurred_at"), ["occurred_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_replay_of_run_id"), ["replay_of_run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_run_id"), ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_decision_audit_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_run_id"))
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_replay_of_run_id"))
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_input_hash"))
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_final_verdict"))
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_correction_id"))
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_alert_id"))
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_action"))
        batch_op.drop_index("ix_soc_decision_audit_run_action")
        batch_op.drop_index("ix_soc_decision_audit_alert_action")
    op.drop_table("soc_decision_audit_log")
