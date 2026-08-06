"""Add PI-03C Skill feedback and improvement backlog.

Revision ID: 0020_skill_improvement_backlog
Revises: 0019_enrichment_executions
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_skill_improvement_backlog"
down_revision: str | Sequence[str] | None = "0019_enrichment_executions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_skill_feedback_observations",
        sa.Column("observation_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("aggregation_key", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("data_class", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=256), nullable=False),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("package_hash", sa.String(length=64), nullable=False),
        sa.Column("scenario_key", sa.String(length=256), nullable=False),
        sa.Column("failure_facet", sa.String(length=64), nullable=False),
        sa.Column("mocked", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("observation_id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint(
            "aggregation_key",
            "source_id",
            name="uq_soc_skill_feedback_aggregation_source",
        ),
    )
    for column in (
        "idempotency_key",
        "aggregation_key",
        "content_hash",
        "tenant_id",
        "data_class",
        "source_type",
        "source_id",
        "skill_name",
        "package_hash",
        "scenario_key",
        "failure_facet",
        "observed_at",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_soc_skill_feedback_observations_{column}"),
            "soc_skill_feedback_observations",
            [column],
        )
    op.create_index(
        "ix_soc_skill_feedback_scope",
        "soc_skill_feedback_observations",
        ["tenant_id", "data_class", "skill_name", "failure_facet"],
    )

    op.create_table(
        "soc_skill_improvement_candidates",
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("aggregation_key", sa.String(length=64), nullable=False),
        sa.Column("aggregation_policy_version", sa.String(length=128), nullable=False),
        sa.Column("candidate_content_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("data_class", sa.String(length=32), nullable=False),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("package_hash", sa.String(length=64), nullable=False),
        sa.Column("scenario_key", sa.String(length=256), nullable=False),
        sa.Column("failure_facet", sa.String(length=64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("mocked", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by_actor_id", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candidate_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("candidate_id"),
        sa.UniqueConstraint("aggregation_key"),
    )
    for column in (
        "aggregation_key",
        "aggregation_policy_version",
        "candidate_content_hash",
        "status",
        "tenant_id",
        "data_class",
        "skill_name",
        "package_hash",
        "scenario_key",
        "failure_facet",
        "reviewed_by_actor_id",
        "reviewed_at",
        "created_at",
        "updated_at",
    ):
        op.create_index(
            op.f(f"ix_soc_skill_improvement_candidates_{column}"),
            "soc_skill_improvement_candidates",
            [column],
        )
    op.create_index(
        "ix_soc_skill_improvement_scope_status",
        "soc_skill_improvement_candidates",
        ["tenant_id", "data_class", "skill_name", "status"],
    )
    op.create_index(
        "ix_soc_skill_improvement_facet_status",
        "soc_skill_improvement_candidates",
        ["failure_facet", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_soc_skill_improvement_facet_status",
        table_name="soc_skill_improvement_candidates",
    )
    op.drop_index(
        "ix_soc_skill_improvement_scope_status",
        table_name="soc_skill_improvement_candidates",
    )
    for column in reversed(
        (
            "aggregation_key",
            "aggregation_policy_version",
            "candidate_content_hash",
            "status",
            "tenant_id",
            "data_class",
            "skill_name",
            "package_hash",
            "scenario_key",
            "failure_facet",
            "reviewed_by_actor_id",
            "reviewed_at",
            "created_at",
            "updated_at",
        )
    ):
        op.drop_index(
            op.f(f"ix_soc_skill_improvement_candidates_{column}"),
            table_name="soc_skill_improvement_candidates",
        )
    op.drop_table("soc_skill_improvement_candidates")

    op.drop_index(
        "ix_soc_skill_feedback_scope",
        table_name="soc_skill_feedback_observations",
    )
    for column in reversed(
        (
            "idempotency_key",
            "aggregation_key",
            "content_hash",
            "tenant_id",
            "data_class",
            "source_type",
            "source_id",
            "skill_name",
            "package_hash",
            "scenario_key",
            "failure_facet",
            "observed_at",
            "created_at",
        )
    ):
        op.drop_index(
            op.f(f"ix_soc_skill_feedback_observations_{column}"),
            table_name="soc_skill_feedback_observations",
        )
    op.drop_table("soc_skill_feedback_observations")
