"""Create SOC approval grants table.

Revision ID: 0005_approval_grants
Revises: 0004_review_queue
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_approval_grants"
down_revision: str | Sequence[str] | None = "0004_review_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_approval_grants",
        sa.Column("approval_grant_id", sa.String(length=64), nullable=False),
        sa.Column("execution_token_id", sa.String(length=64), nullable=False),
        sa.Column("approval_request_id", sa.String(length=64), nullable=False),
        sa.Column("permission_decision_id", sa.String(length=64), nullable=False),
        sa.Column("route", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approved_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("requested_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("approval_reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("consume_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("execution_result_id", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grant_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("approval_grant_id"),
        sa.UniqueConstraint("execution_token_id"),
    )
    with op.batch_alter_table("soc_approval_grants", schema=None) as batch_op:
        batch_op.create_index("ix_soc_approval_grants_action_status", ["action", "status"], unique=False)
        batch_op.create_index("ix_soc_approval_grants_status_expires", ["status", "expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_approval_request_id"), ["approval_request_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_approved_at"), ["approved_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_approved_by_actor_id"), ["approved_by_actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_consumed_at"), ["consumed_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_consume_idempotency_key"), ["consume_idempotency_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_execution_result_id"), ["execution_result_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_execution_token_id"), ["execution_token_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_idempotency_key"), ["idempotency_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_permission_decision_id"), ["permission_decision_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_requested_by_actor_id"), ["requested_by_actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_risk_level"), ["risk_level"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_route"), ["route"], unique=False)
        batch_op.create_index(batch_op.f("ix_soc_approval_grants_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_approval_grants", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_status"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_route"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_risk_level"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_requested_by_actor_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_permission_decision_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_idempotency_key"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_expires_at"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_execution_token_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_execution_result_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_consume_idempotency_key"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_consumed_at"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_approved_by_actor_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_approved_at"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_approval_request_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_grants_action"))
        batch_op.drop_index("ix_soc_approval_grants_status_expires")
        batch_op.drop_index("ix_soc_approval_grants_action_status")
    op.drop_table("soc_approval_grants")
