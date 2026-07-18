"""Add append-only SOC mutation audit log.

Revision ID: 0018_mutation_audit
Revises: 0017_approval_request_lifecycle
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_mutation_audit"
down_revision: str | Sequence[str] | None = "0017_approval_request_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_mutation_audit_log",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=256), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("alert_id", sa.String(length=128), nullable=True),
        sa.Column("queue_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_surface", sa.String(length=32), nullable=False),
        sa.Column("actor_auth_source", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("command_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("result_status", sa.String(length=64), nullable=False),
        sa.Column("result_ref", sa.String(length=256), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
        sa.UniqueConstraint(
            "operation",
            "idempotency_key",
            name="uq_soc_mutation_audit_operation_idempotency",
        ),
    )
    op.create_index(op.f("ix_soc_mutation_audit_log_operation"), "soc_mutation_audit_log", ["operation"])
    op.create_index(op.f("ix_soc_mutation_audit_log_target_type"), "soc_mutation_audit_log", ["target_type"])
    op.create_index(op.f("ix_soc_mutation_audit_log_target_id"), "soc_mutation_audit_log", ["target_id"])
    op.create_index(op.f("ix_soc_mutation_audit_log_run_id"), "soc_mutation_audit_log", ["run_id"])
    op.create_index(op.f("ix_soc_mutation_audit_log_alert_id"), "soc_mutation_audit_log", ["alert_id"])
    op.create_index(op.f("ix_soc_mutation_audit_log_queue_id"), "soc_mutation_audit_log", ["queue_id"])
    op.create_index(op.f("ix_soc_mutation_audit_log_actor_id"), "soc_mutation_audit_log", ["actor_id"])
    op.create_index(op.f("ix_soc_mutation_audit_log_request_id"), "soc_mutation_audit_log", ["request_id"])
    op.create_index(op.f("ix_soc_mutation_audit_log_result_status"), "soc_mutation_audit_log", ["result_status"])
    op.create_index(op.f("ix_soc_mutation_audit_log_result_ref"), "soc_mutation_audit_log", ["result_ref"])
    op.create_index(op.f("ix_soc_mutation_audit_log_occurred_at"), "soc_mutation_audit_log", ["occurred_at"])
    op.create_index(
        "ix_soc_mutation_audit_target",
        "soc_mutation_audit_log",
        ["target_type", "target_id", "occurred_at"],
    )
    op.create_index(
        "ix_soc_mutation_audit_run_operation",
        "soc_mutation_audit_log",
        ["run_id", "operation", "occurred_at"],
    )
    op.create_index(
        "ix_soc_mutation_audit_queue_operation",
        "soc_mutation_audit_log",
        ["queue_id", "operation", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_soc_mutation_audit_queue_operation", table_name="soc_mutation_audit_log")
    op.drop_index("ix_soc_mutation_audit_run_operation", table_name="soc_mutation_audit_log")
    op.drop_index("ix_soc_mutation_audit_target", table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_occurred_at"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_result_ref"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_result_status"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_request_id"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_actor_id"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_queue_id"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_alert_id"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_run_id"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_target_id"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_target_type"), table_name="soc_mutation_audit_log")
    op.drop_index(op.f("ix_soc_mutation_audit_log_operation"), table_name="soc_mutation_audit_log")
    op.drop_table("soc_mutation_audit_log")
