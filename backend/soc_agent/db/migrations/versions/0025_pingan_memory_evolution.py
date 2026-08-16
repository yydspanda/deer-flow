"""Add profile-aware Memory cohorts and operational feedback lineage.

Revision ID: 0025_memory_evolution
Revises: 0024_decision_stages
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_memory_evolution"
down_revision: str | Sequence[str] | None = "0024_decision_stages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table(
        "soc_memory_pattern_observations",
        schema=None,
    ) as batch_op:
        batch_op.add_column(sa.Column("profile_id", sa.String(length=128)))
        batch_op.add_column(sa.Column("profile_version", sa.String(length=128)))
        batch_op.add_column(sa.Column("feature_schema_version", sa.String(length=128)))
        batch_op.add_column(sa.Column("occurrence_key", sa.String(length=64)))
        batch_op.create_index(
            "ix_soc_memory_pattern_observations_profile_id",
            ["profile_id"],
        )
        batch_op.create_index(
            "ix_soc_memory_pattern_observations_occurrence_key",
            ["occurrence_key"],
        )
        batch_op.create_unique_constraint(
            "uq_soc_memory_pattern_aggregation_occurrence",
            ["aggregation_key", "occurrence_key"],
        )

    op.create_table(
        "soc_memory_uses",
        sa.Column("use_id", sa.String(length=64), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128)),
        sa.Column("effect", sa.String(length=32), nullable=False),
        sa.Column("directive_applied", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("use_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_soc_memory_uses_idempotency_key",
        ),
    )
    _create_indexes(
        "soc_memory_uses",
        {
            "ix_soc_memory_uses_idempotency_key": ["idempotency_key"],
            "ix_soc_memory_uses_memory_id": ["memory_id"],
            "ix_soc_memory_uses_run_id": ["run_id"],
            "ix_soc_memory_uses_alert_id": ["alert_id"],
            "ix_soc_memory_uses_tenant_id": ["tenant_id"],
            "ix_soc_memory_uses_effect": ["effect"],
            "ix_soc_memory_uses_directive_applied": ["directive_applied"],
            "ix_soc_memory_uses_created_at": ["created_at"],
            "ix_soc_memory_uses_memory_created": ["memory_id", "created_at"],
            "ix_soc_memory_uses_run_created": ["run_id", "created_at"],
            "ix_soc_memory_uses_alert_created": ["alert_id", "created_at"],
        },
    )

    op.create_table(
        "soc_memory_feedback",
        sa.Column("feedback_id", sa.String(length=64), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("use_id", sa.String(length=64), nullable=False),
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("trust", sa.String(length=32), nullable=False),
        sa.Column("alignment", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("feedback_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_soc_memory_feedback_idempotency_key",
        ),
    )
    _create_indexes(
        "soc_memory_feedback",
        {
            "ix_soc_memory_feedback_idempotency_key": ["idempotency_key"],
            "ix_soc_memory_feedback_use_id": ["use_id"],
            "ix_soc_memory_feedback_memory_id": ["memory_id"],
            "ix_soc_memory_feedback_run_id": ["run_id"],
            "ix_soc_memory_feedback_source": ["source"],
            "ix_soc_memory_feedback_trust": ["trust"],
            "ix_soc_memory_feedback_alignment": ["alignment"],
            "ix_soc_memory_feedback_created_at": ["created_at"],
            "ix_soc_memory_feedback_memory_created": ["memory_id", "created_at"],
            "ix_soc_memory_feedback_run_created": ["run_id", "created_at"],
            "ix_soc_memory_feedback_alignment_created": ["alignment", "created_at"],
        },
    )

    op.create_table(
        "soc_memory_health",
        sa.Column("health_key", sa.String(length=160), primary_key=True),
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("health_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "memory_id",
            "memory_version",
            name="uq_soc_memory_health_memory_version",
        ),
    )
    _create_indexes(
        "soc_memory_health",
        {
            "ix_soc_memory_health_memory_id": ["memory_id"],
            "ix_soc_memory_health_status": ["status"],
            "ix_soc_memory_health_updated_at": ["updated_at"],
            "ix_soc_memory_health_status_updated": ["status", "updated_at"],
        },
    )

    op.create_table(
        "soc_memory_revision_proposals",
        sa.Column("proposal_id", sa.String(length=64), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("source_feedback_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("proposal_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_soc_memory_revision_proposals_idempotency_key",
        ),
    )
    _create_indexes(
        "soc_memory_revision_proposals",
        {
            "ix_soc_memory_revision_proposals_idempotency_key": ["idempotency_key"],
            "ix_soc_memory_revision_proposals_memory_id": ["memory_id"],
            "ix_soc_memory_revision_proposals_source_feedback_id": ["source_feedback_id"],
            "ix_soc_memory_revision_proposals_status": ["status"],
            "ix_soc_memory_revision_proposals_created_at": ["created_at"],
            "ix_soc_memory_revision_memory_status": ["memory_id", "status", "created_at"],
        },
    )


def downgrade() -> None:
    op.drop_table("soc_memory_revision_proposals")
    op.drop_table("soc_memory_health")
    op.drop_table("soc_memory_feedback")
    op.drop_table("soc_memory_uses")
    with op.batch_alter_table(
        "soc_memory_pattern_observations",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_soc_memory_pattern_aggregation_occurrence",
            type_="unique",
        )
        batch_op.drop_index("ix_soc_memory_pattern_observations_occurrence_key")
        batch_op.drop_index("ix_soc_memory_pattern_observations_profile_id")
        batch_op.drop_column("occurrence_key")
        batch_op.drop_column("feature_schema_version")
        batch_op.drop_column("profile_version")
        batch_op.drop_column("profile_id")


def _create_indexes(
    table: str,
    indexes: dict[str, list[str]],
) -> None:
    for name, columns in indexes.items():
        op.create_index(name, table, columns)
