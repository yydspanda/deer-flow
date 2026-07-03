"""Add idempotency key to SOC decision audit log.

Revision ID: 0007_audit_idempotency_key
Revises: 0006_approval_requests
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_audit_idempotency_key"
down_revision: str | Sequence[str] | None = "0006_approval_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("soc_decision_audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=256), nullable=True))
        batch_op.create_index(batch_op.f("ix_soc_decision_audit_log_idempotency_key"), ["idempotency_key"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("soc_decision_audit_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_soc_decision_audit_log_idempotency_key"))
        batch_op.drop_column("idempotency_key")
