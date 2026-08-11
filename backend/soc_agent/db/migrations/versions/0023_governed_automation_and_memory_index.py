"""Add governed automation lineage and relevance-first memory facets.

Revision ID: 0023_governed_automation
Revises: 0022_tenant_policy_decisions
Create Date: 2026-08-11
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_governed_automation"
down_revision: str | Sequence[str] | None = "0022_tenant_policy_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_memory_facets()
    _backfill_memory_facets()
    _create_decision_transitions()
    _create_disposition_transitions()
    _create_action_authorizations()
    _create_action_executions()


def downgrade() -> None:
    op.drop_table("soc_action_executions")
    op.drop_table("soc_action_authorizations")
    op.drop_table("soc_disposition_transitions")
    op.drop_table("soc_decision_transitions")
    op.drop_table("soc_memory_record_facets")


def _create_memory_facets() -> None:
    op.create_table(
        "soc_memory_record_facets",
        sa.Column("facet_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("facet_key", sa.String(length=128), nullable=False),
        sa.Column("facet_value", sa.Text(), nullable=False),
        sa.Column("facet_value_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("facet_id"),
        sa.UniqueConstraint(
            "memory_id",
            "facet_key",
            "facet_value_hash",
            name="uq_soc_memory_record_facet",
        ),
    )
    op.create_index(
        "ix_soc_memory_record_facets_memory_id",
        "soc_memory_record_facets",
        ["memory_id"],
    )
    op.create_index(
        "ix_soc_memory_record_facets_lookup",
        "soc_memory_record_facets",
        ["facet_key", "facet_value_hash", "memory_id"],
    )


def _backfill_memory_facets() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT memory_id, record_payload FROM soc_memory_records")).mappings()
    table = sa.table(
        "soc_memory_record_facets",
        sa.column("memory_id", sa.String()),
        sa.column("facet_key", sa.String()),
        sa.column("facet_value", sa.Text()),
        sa.column("facet_value_hash", sa.String()),
    )
    values: list[dict[str, str]] = []
    for row in rows:
        payload = row["record_payload"]
        if isinstance(payload, (str, bytes, bytearray)):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        if not isinstance(payload, dict):
            continue
        exact: set[tuple[str, str]] = set()
        facets = payload.get("facets")
        if isinstance(facets, dict):
            for raw_key, raw_values in facets.items():
                key = str(raw_key).strip().casefold()
                if not key:
                    continue
                facet_values = raw_values if isinstance(raw_values, list) else [raw_values]
                for raw_value in facet_values:
                    value = str(raw_value).strip()
                    if value:
                        exact.add((key, value))
        evidence_refs = payload.get("evidence_refs")
        if isinstance(evidence_refs, list):
            for raw_value in evidence_refs:
                value = str(raw_value).strip()
                if value:
                    exact.add(("__evidence_ref__", value))
        values.extend(
            {
                "memory_id": str(row["memory_id"]),
                "facet_key": key,
                "facet_value": value,
                "facet_value_hash": _value_hash(value),
            }
            for key, value in sorted(exact)
        )
    if values:
        op.bulk_insert(table, values)


def _create_decision_transitions() -> None:
    op.create_table(
        "soc_decision_transitions",
        sa.Column("transition_id", sa.String(length=64), nullable=False),
        sa.Column("transition_key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("before_verdict", sa.String(length=32), nullable=False),
        sa.Column("after_verdict", sa.String(length=32), nullable=False),
        sa.Column("before_needs_review", sa.Boolean(), nullable=False),
        sa.Column("after_needs_review", sa.Boolean(), nullable=False),
        sa.Column("transition_kind", sa.String(length=32), nullable=False),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transition_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("transition_id"),
        sa.UniqueConstraint("transition_key"),
    )
    _indexes(
        "soc_decision_transitions",
        (
            "transition_key",
            "run_id",
            "alert_id",
            "tenant_id",
            "before_verdict",
            "after_verdict",
            "transition_kind",
            "policy_id",
            "policy_version",
            "policy_hash",
            "created_by_actor_id",
            "created_at",
        ),
    )
    op.create_index(
        "ix_soc_decision_transition_run_created",
        "soc_decision_transitions",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_soc_decision_transition_alert_created",
        "soc_decision_transitions",
        ["alert_id", "created_at"],
    )


def _create_disposition_transitions() -> None:
    op.create_table(
        "soc_disposition_transitions",
        sa.Column("transition_id", sa.String(length=64), nullable=False),
        sa.Column("transition_key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("decision_transition_id", sa.String(length=64), nullable=False),
        sa.Column("before_disposition", sa.String(length=64), nullable=True),
        sa.Column("after_disposition", sa.String(length=64), nullable=True),
        sa.Column("transition_kind", sa.String(length=32), nullable=False),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("selected_rule_id", sa.String(length=128), nullable=True),
        sa.Column("created_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transition_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("transition_id"),
        sa.UniqueConstraint("transition_key"),
    )
    _indexes(
        "soc_disposition_transitions",
        (
            "transition_key",
            "run_id",
            "alert_id",
            "tenant_id",
            "decision_transition_id",
            "before_disposition",
            "after_disposition",
            "transition_kind",
            "policy_id",
            "policy_version",
            "selected_rule_id",
            "created_by_actor_id",
            "created_at",
        ),
    )
    op.create_index(
        "ix_soc_disposition_transition_run_created",
        "soc_disposition_transitions",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_soc_disposition_transition_alert_created",
        "soc_disposition_transitions",
        ["alert_id", "created_at"],
    )


def _create_action_authorizations() -> None:
    op.create_table(
        "soc_action_authorizations",
        sa.Column("authorization_id", sa.String(length=64), nullable=False),
        sa.Column("authorization_key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("decision_transition_id", sa.String(length=64), nullable=False),
        sa.Column("disposition_transition_id", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("route", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=256), nullable=False),
        sa.Column("adapter_id", sa.String(length=256), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_value", sa.Text(), nullable=False),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("selected_rule_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorized_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorization_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("authorization_id"),
        sa.UniqueConstraint("authorization_key"),
    )
    _indexes(
        "soc_action_authorizations",
        (
            "authorization_key",
            "run_id",
            "alert_id",
            "tenant_id",
            "decision_transition_id",
            "disposition_transition_id",
            "mode",
            "decision",
            "route",
            "action",
            "adapter_id",
            "target_type",
            "policy_id",
            "policy_version",
            "selected_rule_id",
            "expires_at",
            "authorized_by_actor_id",
            "created_at",
        ),
    )
    op.create_index(
        "ix_soc_action_authorization_run_created",
        "soc_action_authorizations",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_soc_action_authorization_alert_created",
        "soc_action_authorizations",
        ["alert_id", "created_at"],
    )
    op.create_index(
        "ix_soc_action_authorization_decision_created",
        "soc_action_authorizations",
        ["decision", "created_at"],
    )


def _create_action_executions() -> None:
    op.create_table(
        "soc_action_executions",
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("execution_key", sa.String(length=64), nullable=False),
        sa.Column("authorization_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=128), nullable=False),
        sa.Column("route", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=256), nullable=False),
        sa.Column("adapter_id", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("external_request_id", sa.String(length=512), nullable=True),
        sa.Column("executed_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("execution_id"),
        sa.UniqueConstraint("execution_key"),
    )
    _indexes(
        "soc_action_executions",
        (
            "execution_key",
            "authorization_id",
            "run_id",
            "alert_id",
            "route",
            "action",
            "adapter_id",
            "status",
            "idempotency_key",
            "external_request_id",
            "executed_by_actor_id",
            "started_at",
            "ended_at",
        ),
    )
    op.create_index(
        "ix_soc_action_execution_run_started",
        "soc_action_executions",
        ["run_id", "started_at"],
    )
    op.create_index(
        "ix_soc_action_execution_authorization_started",
        "soc_action_executions",
        ["authorization_id", "started_at"],
    )
    op.create_index(
        "ix_soc_action_execution_status_started",
        "soc_action_executions",
        ["status", "started_at"],
    )


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def _value_hash(value: str) -> str:
    return hashlib.sha256(value.strip().casefold().encode("utf-8")).hexdigest()
