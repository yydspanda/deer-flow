"""Create SOC analysis run table.

Revision ID: 0001_soc_analysis_runs
Revises:
Create Date: 2026-06-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_soc_analysis_runs"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_analysis_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("replay_of_run_id", sa.String(length=64), nullable=True),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=True),
        sa.Column("run_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    with op.batch_alter_table("soc_analysis_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_soc_analysis_runs_alert_id"), ["alert_id"], unique=False)
        batch_op.create_index("ix_soc_analysis_runs_alert_status", ["alert_id", "status"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_analysis_runs_input_hash"), ["input_hash"], unique=False)
        batch_op.create_index("ix_soc_analysis_runs_replay_source", ["replay_of_run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_analysis_runs_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_analysis_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_analysis_runs_status"))
        batch_op.drop_index("ix_soc_analysis_runs_replay_source")
        batch_op.drop_index(batch_op.f("ix_soc_analysis_runs_input_hash"))
        batch_op.drop_index("ix_soc_analysis_runs_alert_status")
        batch_op.drop_index(batch_op.f("ix_soc_analysis_runs_alert_id"))
    op.drop_table("soc_analysis_runs")
