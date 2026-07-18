"""Add atomic SOC approval request lifecycle fields.

Revision ID: 0017_approval_request_lifecycle
Revises: 0016_disposition_evaluation
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_approval_request_lifecycle"
down_revision: str | Sequence[str] | None = "0016_disposition_evaluation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("soc_approval_requests", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("resolved_by_actor_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("resolution_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("resolution_idempotency_key", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("approval_grant_id", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_soc_approval_requests_resolved_at"), ["resolved_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_soc_approval_requests_resolved_by_actor_id"),
            ["resolved_by_actor_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_soc_approval_requests_resolution_idempotency_key"),
            ["resolution_idempotency_key"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_soc_approval_requests_approval_grant_id"),
            ["approval_grant_id"],
            unique=False,
        )

    with op.batch_alter_table("soc_approval_grants", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_soc_approval_grants_request", ["approval_request_id"])


def downgrade() -> None:
    with op.batch_alter_table("soc_approval_grants", schema=None) as batch_op:
        batch_op.drop_constraint("uq_soc_approval_grants_request", type_="unique")

    with op.batch_alter_table("soc_approval_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_approval_grant_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_resolution_idempotency_key"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_resolved_by_actor_id"))
        batch_op.drop_index(batch_op.f("ix_soc_approval_requests_resolved_at"))
        batch_op.drop_column("approval_grant_id")
        batch_op.drop_column("resolution_idempotency_key")
        batch_op.drop_column("resolution_reason")
        batch_op.drop_column("resolved_by_actor_id")
        batch_op.drop_column("resolved_at")
