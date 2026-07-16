"""ORM models for SOC Agent persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from soc_agent.db.base import SocBase


class SocAnalysisRunRow(SocBase):
    """Persisted SOC analysis run.

    The full Pydantic run is stored in ``run_payload`` so schema evolution can
    proceed at the contract layer while indexed columns support common lookups.
    """

    __tablename__ = "soc_analysis_runs"
    __table_args__ = (
        Index("ix_soc_analysis_runs_alert_status", "alert_id", "status"),
        Index("ix_soc_analysis_runs_replay_source", "replay_of_run_id"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    replay_of_run_id: Mapped[str | None] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    run_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SocDecisionAuditLogRow(SocBase):
    """Structured audit record for SOC run decisions and corrections."""

    __tablename__ = "soc_decision_audit_log"
    __table_args__ = (
        Index("ix_soc_decision_audit_run_action", "run_id", "action"),
        Index("ix_soc_decision_audit_alert_action", "alert_id", "action"),
    )

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    alert_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_surface: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), index=True)
    previous_verdict: Mapped[str | None] = mapped_column(String(32))
    final_verdict: Mapped[str | None] = mapped_column(String(32), index=True)
    confidence: Mapped[float | None]
    replay_of_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    correction_id: Mapped[str | None] = mapped_column(String(64), index=True)
    record_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocAlertSummaryRow(SocBase):
    """Queryable alert summary for queues, dedup, and review surfaces."""

    __tablename__ = "soc_alert_summaries"
    __table_args__ = (
        Index("ix_soc_alert_summaries_alert_status", "alert_id", "status"),
        Index("ix_soc_alert_summaries_review_updated", "needs_review", "updated_at"),
        Index("ix_soc_alert_summaries_detection_updated", "detection_key", "updated_at"),
        Index("ix_soc_alert_summaries_source_updated", "source_type", "updated_at"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    source_system: Mapped[str | None] = mapped_column(String(128), index=True)
    detection_key: Mapped[str | None] = mapped_column(String(256), index=True)
    rule_code: Mapped[str | None] = mapped_column(String(128), index=True)
    rule_name: Mapped[str | None] = mapped_column(String(256), index=True)
    severity: Mapped[str | None] = mapped_column(String(32), index=True)
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    entity_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(32), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, index=True, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    input_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    replay_of_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    summary_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocReviewQueueRow(SocBase):
    """Human review queue item derived from SOC alert summaries."""

    __tablename__ = "soc_review_queue"
    __table_args__ = (
        Index("ix_soc_review_queue_status_priority", "status", "priority", "updated_at"),
        Index("ix_soc_review_queue_alert_status", "alert_id", "status"),
    )

    queue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    alert_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    priority: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    source_system: Mapped[str | None] = mapped_column(String(128), index=True)
    rule_code: Mapped[str | None] = mapped_column(String(128), index=True)
    rule_name: Mapped[str | None] = mapped_column(String(256), index=True)
    severity: Mapped[str | None] = mapped_column(String(32), index=True)
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    verdict: Mapped[str | None] = mapped_column(String(32), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    entity_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    closed_by_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    close_reason: Mapped[str | None] = mapped_column(Text)
    item_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocApprovalGrantRow(SocBase):
    """Approved high-risk action grant and consume state."""

    __tablename__ = "soc_approval_grants"
    __table_args__ = (
        Index("ix_soc_approval_grants_status_expires", "status", "expires_at"),
        Index("ix_soc_approval_grants_action_status", "action", "status"),
    )

    approval_grant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_token_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    approval_request_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    permission_decision_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    route: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    approved_by_actor_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    requested_by_actor_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    approval_reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    consume_idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    execution_result_id: Mapped[str | None] = mapped_column(String(64), index=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    grant_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocApprovalRequestRow(SocBase):
    """Pending high-risk action approval request from daemon, agent, or API."""

    __tablename__ = "soc_approval_requests"
    __table_args__ = (
        Index("ix_soc_approval_requests_status_created", "status", "created_at"),
        Index("ix_soc_approval_requests_action_status", "action", "status"),
    )

    approval_request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    permission_decision_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    route: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    requested_by_actor_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocInvestigationEvidenceRow(SocBase):
    """Investigation evidence produced by bounded read-only SOC actions."""

    __tablename__ = "soc_investigation_evidence"
    __table_args__ = (
        Index("ix_soc_investigation_evidence_queue_created", "queue_id", "created_at"),
        Index("ix_soc_investigation_evidence_run_created", "run_id", "created_at"),
        Index("ix_soc_investigation_evidence_alert_created", "alert_id", "created_at"),
        Index("ix_soc_investigation_evidence_action_created", "action", "created_at"),
    )

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    route: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    queue_id: Mapped[str | None] = mapped_column(String(64), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    alert_id: Mapped[str | None] = mapped_column(String(128), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_proposal_id: Mapped[str | None] = mapped_column(String(64), index=True)
    context_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    evidence_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocExternalDispositionRow(SocBase):
    """External ticket/case disposition feedback synchronized into SOC review context."""

    __tablename__ = "soc_external_dispositions"
    __table_args__ = (
        Index("ix_soc_external_dispositions_case_created", "external_system", "external_case_id", "created_at"),
        Index("ix_soc_external_dispositions_run_created", "target_run_id", "created_at"),
        Index("ix_soc_external_dispositions_alert_created", "target_alert_id", "created_at"),
        Index("ix_soc_external_dispositions_queue_created", "target_queue_id", "created_at"),
        Index("ix_soc_external_dispositions_apply_created", "apply_status", "created_at"),
    )

    disposition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    external_system: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    external_case_id: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(256), index=True)
    source_version: Mapped[str | None] = mapped_column(String(256))
    external_status: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    canonical_status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    apply_status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), unique=True, index=True, nullable=False)
    target_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    target_alert_id: Mapped[str | None] = mapped_column(String(128), index=True)
    target_queue_id: Mapped[str | None] = mapped_column(String(64), index=True)
    matched_by: Mapped[str | None] = mapped_column(String(64), index=True)
    audit_id: Mapped[str | None] = mapped_column(String(64), index=True)
    correction_id: Mapped[str | None] = mapped_column(String(64), index=True)
    memory_candidate_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    disposition_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocMemoryCandidateRow(SocBase):
    """Reviewable SOC memory candidate that cannot affect runtime decisions."""

    __tablename__ = "soc_memory_candidates"
    __table_args__ = (
        Index("ix_soc_memory_candidates_status_created", "status", "created_at"),
        Index("ix_soc_memory_candidates_tenant_status", "tenant_scope", "tenant_id", "status"),
        Index("ix_soc_memory_candidates_run_created", "source_run_id", "created_at"),
        Index("ix_soc_memory_candidates_alert_created", "source_alert_id", "created_at"),
        Index("ix_soc_memory_candidates_queue_created", "source_queue_id", "created_at"),
        Index("ix_soc_memory_candidates_source_created", "source_type", "created_at"),
    )

    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_artifact: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tenant_scope: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source_surface: Mapped[str | None] = mapped_column(String(32), index=True)
    source_id: Mapped[str | None] = mapped_column(String(256), index=True)
    source_doc: Mapped[str | None] = mapped_column(String(256), index=True)
    source_section: Mapped[str | None] = mapped_column(String(256))
    capability_card_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_alert_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_queue_id: Mapped[str | None] = mapped_column(String(64), index=True)
    correction_id: Mapped[str | None] = mapped_column(String(64), index=True)
    eval_sample_id: Mapped[str | None] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(512), unique=True, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    decision_impact: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    runtime_decision_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_owner: Mapped[str | None] = mapped_column(String(128), index=True)
    reviewed_by_actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    candidate_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocMemoryRecordRow(SocBase):
    """Confirmed SOC memory record; retrieval policy is still disabled."""

    __tablename__ = "soc_memory_records"
    __table_args__ = (
        Index("ix_soc_memory_records_status_updated", "status", "updated_at"),
        Index("ix_soc_memory_records_tenant_status", "tenant_scope", "tenant_id", "status"),
        Index("ix_soc_memory_records_type_status", "memory_type", "status"),
    )

    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_artifact: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tenant_scope: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_candidate_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_alert_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_queue_id: Mapped[str | None] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    facets_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    retrieval_enabled: Mapped[bool] = mapped_column(Boolean, index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_by_actor_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    deprecated_by_actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    record_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocGovernedContextFactRow(SocBase):
    """One immutable version in a governed operational-context fact stream."""

    __tablename__ = "soc_governed_context_facts"
    __table_args__ = (
        UniqueConstraint("fact_id", "version", name="uq_soc_governed_context_fact_version"),
        Index(
            "ix_soc_governed_context_scope_status",
            "tenant_id",
            "environment",
            "fact_type",
            "status",
            "is_latest",
        ),
        Index(
            "ix_soc_governed_context_validity",
            "tenant_id",
            "environment",
            "valid_from",
            "valid_until",
        ),
    )

    fact_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fact_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    current_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    is_latest: Mapped[bool] = mapped_column(Boolean, index=True, nullable=False)
    fact_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    environment: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(256))
    source_fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    changed_by_actor_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    reviewed_by_actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    fact_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocNormalizationSchemaBaselineRow(SocBase):
    """Approved structural fingerprints for one normalization parser scope."""

    __tablename__ = "soc_normalization_schema_baselines"
    __table_args__ = (
        Index(
            "ix_soc_normalization_baseline_scope_status",
            "tenant_id",
            "source_system",
            "adapter",
            "parser_name",
            "parser_version",
            "status",
        ),
    )

    baseline_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_system: Mapped[str | None] = mapped_column(String(128), index=True)
    adapter: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    parser_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    accepted_fingerprints: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    approved_by_actor_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    baseline_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocNormalizationMaintenanceIssueRow(SocBase):
    """Deduplicated source-parser maintenance issue."""

    __tablename__ = "soc_normalization_maintenance_issues"
    __table_args__ = (
        Index("ix_soc_normalization_issue_status_seen", "status", "last_seen_at"),
        Index("ix_soc_normalization_issue_scope_status", "tenant_id", "source_system", "status"),
    )

    issue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    issue_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_system: Mapped[str | None] = mapped_column(String(128), index=True)
    adapter: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    parser_name: Mapped[str | None] = mapped_column(String(128), index=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), index=True)
    schema_fingerprint: Mapped[str | None] = mapped_column(String(128), index=True)
    source_path: Mapped[str | None] = mapped_column(String(512))
    expected_target: Mapped[str | None] = mapped_column(String(256), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    alert_id: Mapped[str | None] = mapped_column(String(128), index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    acknowledged_by_actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolved_by_actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text)
    issue_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
