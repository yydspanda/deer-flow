"""Create SOC approval requests table.

Revision ID: 0006_approval_requests
Revises: 0005_approval_grants
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_approval_requests"
down_revision: str | Sequence[str] | None = "0005_approval_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_approval_requests",
        sa.Column("approval_request_id", sa.String(length=64), nullable=False),
        sa.Column("permission_decision_id", sa.String(length=64), nullable=False),
        sa.Column("route", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("approval_request_id"),
    )
    with op.batch_alter_table("soc_approval_requests", schema=None) as batch_op:
        batch_op.create_index("ix_soc_approval_requests_action_status", ["action", "status"], unique=False)
        batch_op.create_index("ix_soc_approval_requests_status_created", ["status", "created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_requests_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_requests_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_requests_permission_decision_id"), ["permission_decision_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_requests_requested_by_actor_id"), ["requested_by_actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_requests_risk_level"), ["risk_level"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_requests_route"), ["route"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_requests_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_approval_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_status"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_route"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_risk_level"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_requested_by_actor_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_permission_decision_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_created_at"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_action"))
        batch_op.drop_index("ix_soc_approval_requests_status_created")
        batch_op.drop_index("ix_soc_approval_requests_action_status")
    op.drop_table("soc_approval_requests")
