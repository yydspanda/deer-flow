"""Add indexed SOC effectiveness and model-usage projections.

Revision ID: 0026_effectiveness
Revises: 0025_memory_evolution
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_effectiveness"
down_revision: str | Sequence[str] | None = "0025_memory_evolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("soc_analysis_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("analysis_verdict", sa.String(length=32)))
        batch_op.add_column(sa.Column("runtime_decision_verdict", sa.String(length=32)))
        batch_op.add_column(sa.Column("total_duration_ms", sa.Integer()))
        batch_op.add_column(sa.Column("provider_call_count", sa.Integer()))
        batch_op.add_column(sa.Column("input_tokens", sa.Integer()))
        batch_op.add_column(sa.Column("output_tokens", sa.Integer()))
        batch_op.add_column(sa.Column("total_tokens", sa.Integer()))
        batch_op.add_column(sa.Column("usage_measurement_status", sa.String(length=32)))
        batch_op.add_column(sa.Column("output_quality_status", sa.String(length=32)))
        batch_op.add_column(sa.Column("repair_applied", sa.Boolean()))
        batch_op.add_column(sa.Column("deterministic_fallback_used", sa.Boolean()))
        batch_op.add_column(sa.Column("degraded_section_count", sa.Integer()))
        batch_op.create_index("ix_soc_analysis_runs_analysis_verdict", ["analysis_verdict"])
        batch_op.create_index("ix_soc_analysis_runs_runtime_decision_verdict", ["runtime_decision_verdict"])
        batch_op.create_index("ix_soc_analysis_runs_usage_measurement_status", ["usage_measurement_status"])
        batch_op.create_index("ix_soc_analysis_runs_output_quality_status", ["output_quality_status"])
        batch_op.create_index("ix_soc_analysis_runs_repair_applied", ["repair_applied"])
        batch_op.create_index("ix_soc_analysis_runs_deterministic_fallback_used", ["deterministic_fallback_used"])

    with op.batch_alter_table("soc_decision_audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("confidence_source", sa.String(length=64)))
        batch_op.create_index("ix_soc_decision_audit_log_confidence_source", ["confidence_source"])

    with op.batch_alter_table("soc_external_dispositions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trust_level", sa.String(length=16)))
        batch_op.create_index("ix_soc_external_dispositions_trust_level", ["trust_level"])


def downgrade() -> None:
    with op.batch_alter_table("soc_external_dispositions", schema=None) as batch_op:
        batch_op.drop_index("ix_soc_external_dispositions_trust_level")
        batch_op.drop_column("trust_level")

    with op.batch_alter_table("soc_decision_audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_soc_decision_audit_log_confidence_source")
        batch_op.drop_column("confidence_source")

    with op.batch_alter_table("soc_analysis_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_soc_analysis_runs_deterministic_fallback_used")
        batch_op.drop_index("ix_soc_analysis_runs_repair_applied")
        batch_op.drop_index("ix_soc_analysis_runs_output_quality_status")
        batch_op.drop_index("ix_soc_analysis_runs_usage_measurement_status")
        batch_op.drop_index("ix_soc_analysis_runs_runtime_decision_verdict")
        batch_op.drop_index("ix_soc_analysis_runs_analysis_verdict")
        batch_op.drop_column("degraded_section_count")
        batch_op.drop_column("deterministic_fallback_used")
        batch_op.drop_column("repair_applied")
        batch_op.drop_column("output_quality_status")
        batch_op.drop_column("usage_measurement_status")
        batch_op.drop_column("total_tokens")
        batch_op.drop_column("output_tokens")
        batch_op.drop_column("input_tokens")
        batch_op.drop_column("provider_call_count")
        batch_op.drop_column("total_duration_ms")
        batch_op.drop_column("runtime_decision_verdict")
        batch_op.drop_column("analysis_verdict")
