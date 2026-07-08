"""Pydantic contracts for Phase 1 SOC Agent runtime boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class Verdict(StrEnum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    SUSPICIOUS = "suspicious"
    UNKNOWN = "unknown"
    NEEDS_REVIEW = "needs_review"


class AnalysisRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    SUCCESS = "success"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    ROLLED_BACK = "rolled_back"
    REPLAYED = "replayed"


class PipelineStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SKIPPED = "skipped"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    SERVICE = "service"


class EntrySurface(StrEnum):
    CLI = "cli"
    API = "api"
    CHANNEL = "channel"
    DAEMON = "daemon"
    TUI = "tui"
    WEB = "web"
    TEST = "test"


class SocEventType(StrEnum):
    ANALYSIS_REQUESTED = "analysis.requested"
    ANALYSIS_COMPLETED = "analysis.completed"
    ANALYSIS_FAILED = "analysis.failed"
    EXTERNAL_DISPOSITION_RECEIVED = "external_disposition.received"
    REVIEW_CORRECTED = "review.corrected"
    REVIEW_REQUESTED = "review.requested"
    MEMORY_UPDATED = "memory.updated"


class AuditAction(StrEnum):
    ANALYSIS = "analysis"
    REPLAY = "replay"
    CORRECTION = "correction"
    EXTERNAL_DISPOSITION = "external_disposition"


class ReviewQueueStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ReviewQueuePriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SocMemoryCandidateStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    CONFIRMED_CANDIDATE = "confirmed_candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"
    EXPIRED = "expired"


class SocMemoryCandidateReviewDecision(StrEnum):
    CONFIRM_CANDIDATE = "confirm_candidate"
    CONFIRM = "confirm"
    REJECT = "reject"
    DEPRECATE = "deprecate"
    EXPIRE = "expire"


class SocMemoryRecordStatus(StrEnum):
    CONFIRMED = "confirmed"
    DEPRECATED = "deprecated"
    EXPIRED = "expired"


class SocMemoryCandidateType(StrEnum):
    PROCEDURE = "procedure"
    DETECTION_LESSON = "detection_lesson"
    BENIGN_PATTERN = "benign_pattern"
    ENVIRONMENT_FACT = "environment_fact"
    IDENTITY_PATTERN = "identity_pattern"
    RESPONSE_POLICY_HINT = "response_policy_hint"
    NEGATIVE_MEMORY = "negative_memory"
    ADAPTER_MAPPING = "adapter_mapping"
    EVAL_FIXTURE = "eval_fixture"


class SocMemoryTargetArtifact(StrEnum):
    PUBLIC_SKILL = "public_skill"
    TENANT_MEMORY = "tenant_memory"
    ADAPTER_MAPPING = "adapter_mapping"
    POLICY_CONFIG = "policy_config"
    NORMALIZER = "normalizer"
    DOMAIN_HANDLER = "domain_handler"
    EVAL_FIXTURE = "eval_fixture"
    PROMPT_CONTEXT = "prompt_context"
    EXTERNAL_SYNC = "external_sync"


class SocMemoryDecisionImpact(StrEnum):
    NONE = "none"
    REVIEW_HINT = "review_hint"
    ROUTING_HINT = "routing_hint"
    SUPPRESSION_HINT = "suppression_hint"
    RESPONSE_POLICY_HINT = "response_policy_hint"


class SocMemoryCandidateSourceType(StrEnum):
    PINGAN_DOC = "pingan_doc"
    ANALYSIS_RUN = "analysis_run"
    CORRECTION = "correction"
    DOMAIN_FINDING = "domain_finding"
    EXTERNAL_DISPOSITION = "external_disposition"
    MANUAL_NOTE = "manual_note"
    EVAL_FIXTURE = "eval_fixture"


class SocExternalDispositionCanonicalStatus(StrEnum):
    CLOSED_TRUE_POSITIVE = "closed_true_positive"
    CLOSED_FALSE_POSITIVE = "closed_false_positive"
    CLOSED_BENIGN_TRUE_POSITIVE = "closed_benign_true_positive"
    SUPPRESSED = "suppressed"
    ESCALATED = "escalated"
    IGNORED = "ignored"
    DUPLICATE = "duplicate"
    UNKNOWN = "unknown"


class SocExternalDispositionApplyStatus(StrEnum):
    MAPPED = "mapped"
    UNMATCHED = "unmatched"
    IGNORED = "ignored"


class SocDomainName(StrEnum):
    APT = "apt"
    EDR = "edr"
    HIDS = "hids"
    WAF_F5 = "waf_f5"
    GENERIC = "generic"


class SocDomainFindingSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SocDomainFindingDisposition(StrEnum):
    SUSPICIOUS = "suspicious"
    LIKELY_TRUE_POSITIVE = "likely_true_positive"
    LIKELY_FALSE_POSITIVE = "likely_false_positive"
    BENIGN_AUTHORIZED_CANDIDATE = "benign_authorized_candidate"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"


class ActorContext(BaseModel):
    actor_id: str = "anonymous"
    actor_type: ActorType = ActorType.USER
    surface: EntrySurface = EntrySurface.CLI
    roles: list[str] = Field(default_factory=list)


class ServiceRequestContext(BaseModel):
    request_id: str = Field(default_factory=lambda: f"REQ-{uuid4().hex[:12].upper()}")
    actor: ActorContext = Field(default_factory=ActorContext)
    trace_id: str | None = None
    idempotency_key: str | None = None


class SocEvent(BaseModel):
    schema_version: str = "soc.event.v1"
    event_id: str = Field(default_factory=lambda: f"EVT-{uuid4().hex[:12].upper()}")
    event_type: SocEventType
    request_id: str
    run_id: str | None = None
    alert_id: str | None = None
    actor: ActorContext
    occurred_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class SocAgentStreamEvent(BaseModel):
    """DeerFlow-compatible stream event emitted by SOC interactive services."""

    schema_version: str = "soc.agent_stream.v1"
    type: Literal["values", "messages-tuple", "custom", "end"]
    data: dict[str, Any] = Field(default_factory=dict)


class SocAgentChatRequest(BaseModel):
    """One operator message sent to the SOC interactive investigation surface."""

    schema_version: str = "soc.agent_chat_request.v1"
    message: str = Field(min_length=1)
    thread_id: str | None = None
    queue_id: str | None = None
    run_id: str | None = None
    allowed_routes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocAgentChatResponse(BaseModel):
    """Materialized response for headless callers over the same stream contract."""

    schema_version: str = "soc.agent_chat_response.v1"
    thread_id: str
    events: list[SocAgentStreamEvent] = Field(default_factory=list)
    final_text: str = ""


class SocSkillRecommendation(BaseModel):
    """One DeerFlow skill recommendation for a SOC alert or investigation."""

    skill_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    matched_fields: list[str] = Field(default_factory=list)


class SocSkillResolution(BaseModel):
    """Resolved SOC domain skills without loading skill content directly."""

    schema_version: str = "soc.skill_resolution.v1"
    alert_id: str | None = None
    agent_name: str = "soc-triage"
    selected_skills: list[SocSkillRecommendation] = Field(default_factory=list)
    available_agent_skills: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SocSkillContextItem(BaseModel):
    """Compact, auditable skill context injected into bounded SOC prompts."""

    skill_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    matched_fields: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    content_hash: str | None = None
    token_budget: int = Field(default=240, ge=0)


class SocSkillContext(BaseModel):
    """Bounded skill context derived from DeerFlow skill selection."""

    schema_version: str = "soc.skill_context.v1"
    source: str = "soc_skill_resolver"
    selected_skills: list[SocSkillContextItem] = Field(default_factory=list)
    total_token_budget: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)


class SocLeadAgentProfile(BaseModel):
    """DeerFlow custom-agent profile payload recommended for SOC triage."""

    schema_version: str = "soc.lead_agent_profile.v1"
    name: str = "soc-triage"
    description: str
    skills: list[str] = Field(default_factory=list)
    tool_groups: list[str] | None = None
    soul: str


class SocLeadAgentInstallResult(BaseModel):
    """Result of installing the SOC profile into DeerFlow custom-agent storage."""

    schema_version: str = "soc.lead_agent_install_result.v1"
    agent_name: str
    user_id: str
    agent_dir: str
    config_path: str
    soul_path: str
    status: Literal["dry_run", "created", "updated", "skipped"]
    dry_run: bool = False
    overwrite: bool = False
    message: str


class SocLeadAgentReviewContextArtifact(BaseModel):
    """Bounded ReviewQueue context handed to the DeerFlow SOC lead agent."""

    schema_version: str = "soc.lead_agent_review_context_artifact.v1"
    artifact_id: str = Field(default_factory=lambda: f"LCTX-{uuid4().hex[:12].upper()}")
    queue_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    alert_id: str = Field(min_length=1)
    context_hash: str = Field(min_length=1)
    skill_context_hash: str | None = None
    actor: ActorContext | None = None
    review: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    fact_context: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] | None = None
    similar_alerts: list[dict[str, Any]] = Field(default_factory=list)
    action_evidence: list[dict[str, Any]] = Field(default_factory=list)
    external_dispositions: list[dict[str, Any]] = Field(default_factory=list)
    memory_candidates: list[dict[str, Any]] = Field(default_factory=list)
    relevant_memories: dict[str, Any] | None = None
    investigation_view: dict[str, Any] | None = None
    skill_context: SocSkillContext | None = None
    instructions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SocAgentRouteDecision(BaseModel):
    """Whitelisted capability route selected for one SOC chat request."""

    schema_version: str = "soc.agent_route_decision.v1"
    route: str = Field(min_length=1)
    allowed: bool
    reason: str = Field(min_length=1)
    requires_human_approval: bool = False
    input_text: str | None = None


class SocAgentRiskLevel(StrEnum):
    READ_ONLY = "read_only"
    ANALYST_WRITE = "analyst_write"
    HIGH_RISK = "high_risk"
    UNKNOWN = "unknown"


class SocAgentPermissionDecision(BaseModel):
    """Permission decision for one routed SOC Agent action."""

    schema_version: str = "soc.agent_permission_decision.v1"
    decision_id: str = Field(default_factory=lambda: f"PERM-{uuid4().hex[:12].upper()}")
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    allowed: bool
    risk_level: SocAgentRiskLevel = SocAgentRiskLevel.UNKNOWN
    reason: str = Field(min_length=1)
    requires_human_approval: bool = False
    approval_request_id: str | None = None
    policy_version: str = "soc.agent_action_policy.v1"
    actor: ActorContext | None = None


class SocAgentActionProposal(BaseModel):
    """Structured action candidate proposed by a lead agent or skill."""

    schema_version: str = "soc.agent_action_proposal.v1"
    proposal_id: str = Field(default_factory=lambda: f"SAP-{uuid4().hex[:12].upper()}")
    source: Literal["lead_agent", "skill", "deterministic", "mcp"] = "lead_agent"
    thread_id: str | None = None
    queue_id: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    context_hash: str | None = None
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    proposed_by: ActorContext | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SocAgentApprovalRequest(BaseModel):
    """Human approval request for a blocked high-risk SOC Agent action."""

    schema_version: str = "soc.agent_approval_request.v1"
    approval_request_id: str = Field(default_factory=lambda: f"APR-{uuid4().hex[:12].upper()}")
    permission_decision_id: str
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    risk_level: SocAgentRiskLevel
    reason: str = Field(min_length=1)
    requested_by: ActorContext
    source_proposal_id: str | None = None
    action_payload: dict[str, Any] = Field(default_factory=dict)
    context_refs: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending"] = "pending"
    created_at: datetime = Field(default_factory=utc_now)


class SocAgentApprovalGrant(BaseModel):
    """One-time execution grant produced after human approval."""

    schema_version: str = "soc.agent_approval_grant.v1"
    approval_grant_id: str = Field(default_factory=lambda: f"APG-{uuid4().hex[:12].upper()}")
    execution_token_id: str = Field(default_factory=lambda: f"SAT-{uuid4().hex[:16].upper()}")
    approval_request_id: str
    permission_decision_id: str
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    risk_level: SocAgentRiskLevel
    requested_by: ActorContext
    approved_by: ActorContext
    approval_reason: str = Field(min_length=1)
    idempotency_key: str | None = None
    status: Literal["approved", "consumed"] = "approved"
    single_use: Literal[True] = True
    approved_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    consumed_at: datetime | None = None
    consumed_by: ActorContext | None = None
    consume_idempotency_key: str | None = None
    execution_result_id: str | None = None
    execution_result_payload: dict[str, Any] | None = None
    policy_version: str = "soc.agent_action_policy.v1"


class SocAgentActionCommand(BaseModel):
    """Command to execute a registered SOC action adapter."""

    schema_version: str = "soc.agent_action_command.v1"
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    dry_run: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class SocAgentApprovedActionCommand(SocAgentActionCommand):
    """Command to validate an approved high-risk action before execution."""

    schema_version: str = "soc.agent_approved_action_command.v1"
    execution_token_id: str = Field(min_length=1)


class SocAgentActionResult(BaseModel):
    """Result of dispatching an allowed SOC Agent route to a service action."""

    schema_version: str = "soc.agent_action_result.v1"
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status: Literal["success", "denied", "failed"]
    message: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_human_approval: bool = False


class InvestigationEvidence(BaseModel):
    """Investigation evidence produced by bounded SOC service actions."""

    schema_version: str = "soc.investigation_evidence.v1"
    evidence_id: str = Field(default_factory=lambda: f"EVI-{uuid4().hex[:12].upper()}")
    source_type: Literal["read_only_action_result"] = "read_only_action_result"
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status: Literal["success", "denied", "failed"]
    message: str = Field(min_length=1)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    queue_id: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    thread_id: str | None = None
    source_proposal_id: str | None = None
    context_hash: str | None = None
    actor: ActorContext | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SocMemoryCandidateSource(BaseModel):
    """Auditable origin metadata for one proposed SOC memory candidate."""

    source_type: SocMemoryCandidateSourceType
    source_surface: EntrySurface | None = None
    source_id: str | None = None
    source_doc: str | None = None
    source_section: str | None = None
    capability_card_id: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    queue_id: str | None = None
    correction_id: str | None = None
    eval_sample_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_traceable_reference(self) -> SocMemoryCandidateSource:
        references = (
            self.source_id,
            self.source_doc,
            self.capability_card_id,
            self.run_id,
            self.alert_id,
            self.queue_id,
            self.correction_id,
            self.eval_sample_id,
        )
        if not any(references):
            raise ValueError("memory candidate source must include at least one traceable reference")
        return self


class SocMemoryCandidateValidity(BaseModel):
    """Scope and freshness window for candidate knowledge under review."""

    valid_from: datetime = Field(default_factory=utc_now)
    valid_until: datetime | None = None
    review_after_days: int | None = Field(default=None, ge=1)
    notes: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_forward_window(self) -> SocMemoryCandidateValidity:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class SocMemoryCandidateCreateCommand(BaseModel):
    """Command to propose candidate SOC knowledge without confirming it."""

    candidate_type: SocMemoryCandidateType
    target_artifact: SocMemoryTargetArtifact
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tenant_scope: str = Field(default="global", min_length=1)
    tenant_id: str | None = None
    source: SocMemoryCandidateSource
    evidence_refs: list[str] = Field(min_length=1)
    validity: SocMemoryCandidateValidity
    idempotency_key: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    decision_impact: SocMemoryDecisionImpact = SocMemoryDecisionImpact.NONE
    review_owner: str | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocMemoryCandidateReviewCommand(BaseModel):
    """Command to review one SOC memory candidate without bypassing service audit."""

    candidate_id: str = Field(min_length=1)
    decision: SocMemoryCandidateReviewDecision
    reason: str = Field(min_length=1)
    record_summary: str | None = None
    record_content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocMemoryCandidate(BaseModel):
    """Reviewable candidate knowledge item that cannot affect runtime decisions."""

    schema_version: str = "soc.memory_candidate.v1"
    candidate_id: str = Field(default_factory=lambda: f"MC-{uuid4().hex[:12].upper()}")
    candidate_type: SocMemoryCandidateType
    target_artifact: SocMemoryTargetArtifact
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tenant_scope: str = Field(default="global", min_length=1)
    tenant_id: str | None = None
    status: SocMemoryCandidateStatus = SocMemoryCandidateStatus.PENDING_REVIEW
    source: SocMemoryCandidateSource
    evidence_refs: list[str] = Field(min_length=1)
    validity: SocMemoryCandidateValidity
    idempotency_key: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    decision_impact: SocMemoryDecisionImpact = SocMemoryDecisionImpact.NONE
    runtime_decision_allowed: Literal[False] = False
    review_required: Literal[True] = True
    review_owner: str | None = None
    reviewed_by: ActorContext | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    proposed_by: ActorContext | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SocMemoryRecord(BaseModel):
    """Confirmed SOC memory record; retrieval remains disabled until policy is implemented."""

    schema_version: str = "soc.memory_record.v1"
    memory_id: str = Field(default_factory=lambda: f"MEM-{uuid4().hex[:12].upper()}")
    version: int = Field(default=1, ge=1)
    memory_type: SocMemoryCandidateType
    target_artifact: SocMemoryTargetArtifact
    status: SocMemoryRecordStatus = SocMemoryRecordStatus.CONFIRMED
    tenant_scope: str = Field(default="global", min_length=1)
    tenant_id: str | None = None
    source_candidate_id: str
    source: SocMemoryCandidateSource
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(min_length=1)
    validity: SocMemoryCandidateValidity
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    decision_impact: SocMemoryDecisionImpact = SocMemoryDecisionImpact.NONE
    content_hash: str = Field(min_length=1)
    facets_hash: str = Field(min_length=1)
    retrieval_enabled: bool = False
    created_by: ActorContext
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deprecated_by: ActorContext | None = None
    deprecated_at: datetime | None = None
    deprecation_reason: str | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocMemoryQuery(BaseModel):
    """Retrieval query for confirmed SOC memory records.

    Facets are optional by design. Missing topic, detection key, vendor alias,
    scenario, entity, or environment facets lower recall/score but must not
    make retrieval fail.
    """

    schema_version: str = "soc.memory_query.v1"
    memory_types: list[SocMemoryCandidateType] = Field(default_factory=list)
    statuses: list[SocMemoryRecordStatus] = Field(default_factory=lambda: [SocMemoryRecordStatus.CONFIRMED])
    tenant_scope: str | None = None
    tenant_id: str | None = None
    facets: dict[str, list[str]] = Field(default_factory=dict)
    text_terms: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=50)
    candidate_limit: int = Field(default=200, ge=1, le=1000)
    min_score: float = Field(default=1.0, ge=0.0)
    max_tokens: int = Field(default=1200, ge=100, le=10000)
    require_retrieval_enabled: Literal[True] = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("facets", mode="before")
    @classmethod
    def normalize_facets(cls, value: Any) -> dict[str, list[str]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("facets must be an object")
        normalized: dict[str, list[str]] = {}
        for raw_key, raw_values in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            normalized_values = sorted({str(item).strip() for item in values if str(item).strip()})
            if normalized_values:
                normalized[key] = normalized_values
        return normalized

    @field_validator("text_terms", "evidence_refs", mode="before")
    @classmethod
    def normalize_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        return sorted({str(item).strip() for item in values if str(item).strip()})


class SocMemoryMatch(BaseModel):
    """One retrieval-safe memory match with replayable scoring metadata."""

    memory_id: str
    version: int = Field(ge=1)
    record: SocMemoryRecord
    score: float = Field(ge=0.0)
    match_reasons: list[str] = Field(default_factory=list)
    matched_facets: dict[str, list[str]] = Field(default_factory=dict)
    token_estimate: int = Field(ge=1)
    content_hash: str
    facets_hash: str
    retrieval_enabled: Literal[True] = True


class SocMemoryRetrievalResult(BaseModel):
    """Retrieval result that is safe to inspect before prompt injection is enabled."""

    schema_version: str = "soc.memory_retrieval_result.v1"
    policy_version: str = "soc.memory_retrieval_policy.v1"
    query: SocMemoryQuery
    matches: list[SocMemoryMatch] = Field(default_factory=list)
    total_candidate_count: int = Field(default=0, ge=0)
    skipped_retrieval_disabled: int = Field(default=0, ge=0)
    skipped_status: int = Field(default=0, ge=0)
    skipped_expired: int = Field(default=0, ge=0)
    skipped_below_min_score: int = Field(default=0, ge=0)
    returned_count: int = Field(default=0, ge=0)
    total_token_estimate: int = Field(default=0, ge=0)
    max_tokens: int = Field(default=1200, ge=100)
    created_at: datetime = Field(default_factory=utc_now)


class SocMemoryCandidateReviewResult(BaseModel):
    """Result of a candidate review transition."""

    schema_version: str = "soc.memory_candidate_review_result.v1"
    candidate: SocMemoryCandidate
    memory_record: SocMemoryRecord | None = None
    previous_status: SocMemoryCandidateStatus
    decision: SocMemoryCandidateReviewDecision
    reviewed_at: datetime = Field(default_factory=utc_now)


class SocExternalDispositionEvent(BaseModel):
    """Vendor-neutral external ticket/case disposition event."""

    schema_version: str = "soc.external_disposition.v1"
    tenant_id: str | None = None
    external_system: str = Field(min_length=1)
    external_case_id: str = Field(min_length=1)
    source_event_id: str | None = None
    source_version: str | None = None
    external_alert_ref: str | None = None
    soc_alert_id: str | None = None
    soc_run_id: str | None = None
    soc_queue_id: str | None = None
    external_status: str = Field(min_length=1)
    external_reason: str | None = None
    external_tags: list[str] = Field(default_factory=list)
    operator: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime
    raw_payload_hash: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocExternalDispositionStatusMapping(BaseModel):
    """One external status to canonical disposition mapping rule."""

    external_status: str = Field(min_length=1)
    canonical_status: SocExternalDispositionCanonicalStatus
    external_system: str | None = None
    trust_level: Literal["low", "medium", "high"] = "medium"
    apply_to_review: bool = True
    notes: str | None = None


class SocExternalDispositionMappingConfig(BaseModel):
    """Configurable status mapping used by external disposition adapters/services."""

    schema_version: str = "soc.external_disposition_mapping.v1"
    tenant_id: str | None = None
    status_mappings: list[SocExternalDispositionStatusMapping] = Field(default_factory=list)
    default_canonical_status: SocExternalDispositionCanonicalStatus = SocExternalDispositionCanonicalStatus.UNKNOWN


class SocExternalDispositionAdapterConfig(BaseModel):
    """Generic field-path mapping from an external payload to the canonical event."""

    schema_version: str = "soc.external_disposition_adapter.v1"
    external_system: str = Field(min_length=1)
    tenant_id: str | None = None
    field_paths: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocExternalDispositionRecord(BaseModel):
    """Persisted external disposition event and local mapping outcome."""

    schema_version: str = "soc.external_disposition_record.v1"
    disposition_id: str = Field(default_factory=lambda: f"XDISP-{uuid4().hex[:12].upper()}")
    event: SocExternalDispositionEvent
    canonical_status: SocExternalDispositionCanonicalStatus
    apply_status: SocExternalDispositionApplyStatus
    idempotency_key: str = Field(min_length=1)
    target_run_id: str | None = None
    target_alert_id: str | None = None
    target_queue_id: str | None = None
    matched_by: str | None = None
    apply_reason: str = Field(min_length=1)
    audit_id: str | None = None
    correction_id: str | None = None
    memory_candidate_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocExternalDispositionApplyResult(BaseModel):
    """Service result for applying one external disposition event."""

    schema_version: str = "soc.external_disposition_apply_result.v1"
    record: SocExternalDispositionRecord
    idempotent: bool = False
    audit_written: bool = False
    correction_applied: bool = False
    memory_candidate_created: bool = False


class SocAgentActionAdapterDescriptor(BaseModel):
    """Registered adapter capability for an approved SOC action."""

    schema_version: str = "soc.agent_action_adapter_descriptor.v1"
    adapter_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    risk_level: SocAgentRiskLevel = SocAgentRiskLevel.UNKNOWN
    adapter_kind: Literal["noop", "service", "mcp", "http", "script"] = "noop"
    external_side_effect: Literal["none", "read", "write", "destructive"] = "none"
    dry_run_supported: bool = True
    execute_supported: bool = False
    idempotency_required: bool = True
    required_payload_fields: list[str] = Field(default_factory=list)
    required_context_refs: list[str] = Field(default_factory=list)
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocAssetLookupRecord(BaseModel):
    """Read-only asset inventory record returned by the SOC asset lookup adapter."""

    schema_version: str = "soc.asset_lookup_record.v1"
    asset_key: str = Field(min_length=1)
    asset_id: str | None = None
    hostname: str | None = None
    primary_ip: str | None = None
    owner: str | None = None
    business_unit: str | None = None
    environment: str | None = None
    criticality: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"
    source: str = "static"
    attributes: dict[str, Any] = Field(default_factory=dict)


class SocEndpointProcessNode(BaseModel):
    """One process node returned by an endpoint process-tree lookup."""

    pid: int | None = None
    parent_pid: int | None = None
    process_name: str = Field(min_length=1)
    command_line: str | None = None
    user: str | None = None
    risk_tags: list[str] = Field(default_factory=list)


class SocEndpointNetworkConnection(BaseModel):
    """One network connection observed in an endpoint process tree."""

    process_name: str | None = None
    remote_ip: str = Field(min_length=1)
    remote_port: int | None = Field(default=None, ge=1, le=65535)
    direction: Literal["inbound", "outbound", "unknown"] = "unknown"
    protocol: str | None = None


class SocEndpointProcessTreeRecord(BaseModel):
    """Read-only endpoint process-tree record returned by an EDR adapter."""

    schema_version: str = "soc.endpoint_process_tree_record.v1"
    host_key: str = Field(min_length=1)
    hostname: str | None = None
    primary_ip: str | None = None
    process_tree_id: str | None = None
    observed_at: datetime | None = None
    processes: list[SocEndpointProcessNode] = Field(default_factory=list)
    network_connections: list[SocEndpointNetworkConnection] = Field(default_factory=list)
    source: str = "static"
    mocked: bool = False


class SocHostEventContextRecord(BaseModel):
    """Read-only host event context returned by a host/HIDS adapter."""

    schema_version: str = "soc.host_event_context_record.v1"
    host_key: str = Field(min_length=1)
    hostname: str | None = None
    primary_ip: str | None = None
    time_window: str | None = None
    recent_logins: list[dict[str, Any]] = Field(default_factory=list)
    related_commands: list[dict[str, Any]] = Field(default_factory=list)
    source_ips: list[str] = Field(default_factory=list)
    related_events: list[dict[str, Any]] = Field(default_factory=list)
    host_criticality: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"
    source: str = "static"
    mocked: bool = False


class SocThreatIntelReputationRecord(BaseModel):
    """Read-only threat-intelligence reputation for a network entity."""

    schema_version: str = "soc.threat_intel_reputation_record.v1"
    ip: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    score: int | None = Field(default=None, ge=0, le=100)
    last_seen: datetime | None = None
    geo: str | None = None
    source: str = "static"
    expires_at: datetime | None = None
    stale: bool = False
    mocked: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)


class SocSecurityTagRecord(BaseModel):
    """Read-only authorization, maintenance, or security-testing tag evidence."""

    schema_version: str = "soc.security_tag_record.v1"
    entity_key: str = Field(min_length=1)
    entity_type: str | None = None
    labels: list[str] = Field(default_factory=list)
    tag_types: list[str] = Field(default_factory=list)
    is_valid: bool = False
    valid_until: datetime | None = None
    source: str = "static"
    mocked: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)


class SocDaemonMessage(BaseModel):
    """Versioned message envelope consumed by the SOC daemon boundary.

    Real Kafka consumers should decode transport metadata into this contract
    before calling core services. The payload stays source-specific at the
    boundary and is validated by the downstream service selected by ``kind``.
    """

    schema_version: str = "soc.daemon_message.v1"
    message_id: str = Field(default_factory=lambda: f"SDM-{uuid4().hex[:12].upper()}")
    kind: Literal["alert", "approval_request"]
    payload: dict[str, Any] = Field(default_factory=dict)
    topic: str | None = None
    partition: int | None = None
    offset: int | None = None
    key: str | None = None
    received_at: datetime = Field(default_factory=utc_now)


class SocDaemonProcessResult(BaseModel):
    """Result of processing one daemon message through core services."""

    schema_version: str = "soc.daemon_process_result.v1"
    message_id: str
    kind: Literal["alert", "approval_request"]
    status: Literal["processed", "failed"]
    run_id: str | None = None
    alert_id: str | None = None
    analysis_status: str | None = None
    approval_request_id: str | None = None
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AlertSourceType(StrEnum):
    UNKNOWN = "unknown"
    SIEM = "siem"
    EDR = "edr"
    XDR = "xdr"
    HIDS = "hids"
    NIDS = "nids"
    NDR = "ndr"
    WAF = "waf"
    F5 = "f5"
    IAM = "iam"
    CLOUD = "cloud"
    THREAT_INTEL = "threat_intel"
    OTHER = "other"


class EvidenceLayer(StrEnum):
    RAW_MESSAGE = "raw_message"
    RAW_STRUCTURED = "raw_structured"
    PROCESSED_FIELD = "processed_field"
    AGENT_INFERENCE = "agent_inference"
    HUMAN_CONFIRMED = "human_confirmed"


class EvidenceTrustLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class EvidenceInputPolicyName(StrEnum):
    RAW_MESSAGE_FIRST = "raw_message_first"
    STRUCTURED_FALLBACK = "structured_fallback"
    CANONICAL_FIELDS_FIRST = "canonical_fields_first"
    HYBRID_WITH_CONFLICT_CHECK = "hybrid_with_conflict_check"


class EntityKind(StrEnum):
    IP = "ip"
    DOMAIN = "domain"
    URL = "url"
    PROCESS = "process"
    USER = "user"
    HOST = "host"
    FILE_HASH = "file_hash"
    RULE_CODE = "rule_code"
    RULE_NAME = "rule_name"
    RULE = "rule"
    MITRE = "mitre"
    ASSET = "asset"
    BEHAVIOR = "behavior"


class EntityExtractionSource(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    NORMALIZER = "normalizer"
    ANALYST = "analyst"


class AlertSourceRef(BaseModel):
    """Where the alert came from.

    This keeps vendor/product names out of the core detection logic while still
    letting adapters preserve enough source context for memory and audit.
    """

    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    vendor: str | None = None
    product: str | None = None
    integration_name: str | None = None


class DetectionRuleRef(BaseModel):
    """Normalized detection identity.

    ``rule_code`` is a strong optional identifier. ``detection_key`` is the
    runtime-generated fallback key used by memory and lessons when a source does
    not provide stable rule IDs.
    """

    rule_code: str | None = None
    rule_name: str | None = None
    rule_version: str | None = None
    rule_category: str | None = None
    detection_key: str | None = None


class AlertEventRef(BaseModel):
    event_id: str | None = None
    event_time: datetime | None = None
    received_at: datetime = Field(default_factory=utc_now)


class AlertClassification(BaseModel):
    severity: str | None = None
    category: str | None = None
    tactic: list[str] = Field(default_factory=list)
    technique: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class NetworkEntityRef(BaseModel):
    source_ip: str | None = None
    destination_ip: str | None = None
    src_port: int | None = None
    dst_port: int | None = None
    protocol: str | None = None
    direction: str | None = None
    domain: str | None = None
    url: str | None = None


class ProcessEntityRef(BaseModel):
    process_name: str | None = None
    process_path: str | None = None
    command_line: str | None = None
    parent_process_name: str | None = None
    parent_command_line: str | None = None


class UserEntityRef(BaseModel):
    username: str | None = None
    user_id: str | None = None
    um_account: str | None = None
    src_user: str | None = None
    dst_user: str | None = None


class HostEntityRef(BaseModel):
    host_name: str | None = None
    host_id: str | None = None
    asset_id: str | None = None
    asset_group: str | None = None


class FileEntityRef(BaseModel):
    file_name: str | None = None
    file_path: str | None = None
    sha256: str | None = None
    sha1: str | None = None
    md5: str | None = None


class HttpEntityRef(BaseModel):
    method: str | None = None
    host: str | None = None
    path: str | None = None
    url: str | None = None
    status_code: int | None = None
    user_agent: str | None = None
    x_forwarded_for: str | None = None


class ThreatEntityRef(BaseModel):
    iocs: list[str] = Field(default_factory=list)
    campaign: str | None = None
    threat_actor: str | None = None
    malware_family: str | None = None


class AlertEntitySet(BaseModel):
    network: NetworkEntityRef = Field(default_factory=NetworkEntityRef)
    process: ProcessEntityRef = Field(default_factory=ProcessEntityRef)
    user: UserEntityRef = Field(default_factory=UserEntityRef)
    host: HostEntityRef = Field(default_factory=HostEntityRef)
    file: FileEntityRef = Field(default_factory=FileEntityRef)
    http: HttpEntityRef = Field(default_factory=HttpEntityRef)
    threat: ThreatEntityRef = Field(default_factory=ThreatEntityRef)


class EvidenceItem(BaseModel):
    source: str
    description: str
    value: str | int | float | bool | None = None


class EvidenceInputPolicy(BaseModel):
    """Which input should later reasoning nodes treat as the primary evidence.

    This policy is source-adapter output. The runtime can inspect it before
    fact reconstruction, while vendors with clean schemas can omit it.
    """

    name: EvidenceInputPolicyName
    primary_input_path: str | None = None
    fallback_input_path: str | None = None
    selected_input_path: str | None = None
    selected_layer: EvidenceLayer = EvidenceLayer.RAW_STRUCTURED
    fallback_reason: str | None = None
    ignore_processed_fields_for_reasoning: bool = False
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.MEDIUM


class FieldTrust(BaseModel):
    """Trust annotation for one field considered during fact reconstruction."""

    field_path: str
    layer: EvidenceLayer
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.UNKNOWN
    participates_in_fact_reconstruction: bool = True
    reason: str | None = None


class RoleAssignment(BaseModel):
    """Deterministic candidate assignment for one security-investigation role."""

    role: Literal["source", "destination", "attacker", "victim", "impacted_asset", "response_target"]
    value: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_path: str
    source_layer: EvidenceLayer = EvidenceLayer.PROCESSED_FIELD
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.UNKNOWN
    rationale: str = Field(min_length=1)


class ConflictReport(BaseModel):
    """Structured conflict found before LLM analysis or human review."""

    conflict_type: str = Field(min_length=1)
    severity: Literal["info", "warning", "critical"] = "warning"
    description: str = Field(min_length=1)
    involved_fields: list[str] = Field(default_factory=list)
    candidate_values: dict[str, list[str]] = Field(default_factory=dict)


class FactReconstructionResult(BaseModel):
    """Pre-analysis fact layer built from evidence policy and normalized fields."""

    schema_version: str = "soc.fact_reconstruction.v1"
    evidence_policy: EvidenceInputPolicy | None = None
    selected_input_path: str | None = None
    selected_input_available: bool = False
    field_trusts: list[FieldTrust] = Field(default_factory=list)
    role_assignments: list[RoleAssignment] = Field(default_factory=list)
    conflict_reports: list[ConflictReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AlertInput(BaseModel):
    """Canonical alert input accepted by the SOC runtime.

    Source-specific payloads must be converted into this shape by a normalizer
    before they enter pipeline, DB, memory, API response, or Kafka contracts.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.alert.v1"
    tenant_id: str | None = None
    alert_id: str = Field(default_factory=lambda: f"ALT-{uuid4().hex[:12].upper()}")
    source: AlertSourceRef = Field(default_factory=AlertSourceRef)
    detection: DetectionRuleRef = Field(default_factory=DetectionRuleRef)
    event: AlertEventRef = Field(default_factory=AlertEventRef)
    classification: AlertClassification = Field(default_factory=AlertClassification)
    entities: AlertEntitySet = Field(default_factory=AlertEntitySet)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class EntityMention(BaseModel):
    """Normalized entity mention produced by deterministic or LLM extraction."""

    kind: EntityKind
    value: str = Field(min_length=1)
    key: str = Field(min_length=1)
    role: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: EntityExtractionSource = EntityExtractionSource.DETERMINISTIC
    evidence_path: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ExtractedEntities(BaseModel):
    mentions: list[EntityMention] = Field(default_factory=list)
    ips: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    processes: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    rule_codes: list[str] = Field(default_factory=list)
    rule_names: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LLMAnalysisRequest(BaseModel):
    """Bounded input contract for stub or future LLM analysis nodes."""

    schema_version: str = "soc.llm_analysis_request.v1"
    alert_id: str
    source: AlertSourceRef = Field(default_factory=AlertSourceRef)
    detection: DetectionRuleRef = Field(default_factory=DetectionRuleRef)
    classification: AlertClassification = Field(default_factory=AlertClassification)
    canonical_entities: AlertEntitySet = Field(default_factory=AlertEntitySet)
    extracted_entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    fact_reconstruction: FactReconstructionResult = Field(default_factory=FactReconstructionResult)
    primary_evidence_path: str | None = None
    conflict_count: int = Field(default=0, ge=0)
    conflict_types: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    skill_context: SocSkillContext = Field(default_factory=SocSkillContext)


class NormalizationReport(BaseModel):
    """Cheap quality report for deterministic alert normalization."""

    schema_version: str = "soc.normalization_report.v1"
    adapter: str
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    normalized_fields: list[str] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)
    unmapped_field_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class ExtractionReport(BaseModel):
    """Cheap quality report for deterministic entity extraction."""

    schema_version: str = "soc.extraction_report.v1"
    mention_count: int = Field(default=0, ge=0)
    entity_counts: dict[str, int] = Field(default_factory=dict)
    missing_entity_kinds: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NormalizationInspectionResult(BaseModel):
    """Output for inspect-only normalization and entity extraction."""

    schema_version: str = "soc.normalization_inspection.v1"
    alert: AlertInput
    entities: ExtractedEntities
    normalization_report: NormalizationReport
    extraction_report: ExtractionReport


class NormalizationDriftSample(BaseModel):
    """One sample's normalize/extract quality summary for drift triage."""

    path: str
    status: Literal["success", "failed"]
    run_id: str | None = None
    alert_id: str | None = None
    adapter: str | None = None
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)
    entity_counts: dict[str, int] = Field(default_factory=dict)
    missing_entity_kinds: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class NormalizationDriftReport(BaseModel):
    """Batch report for spotting normalization and extraction drift."""

    schema_version: str = "soc.normalization_drift_report.v1"
    sample_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    adapter_counts: dict[str, int] = Field(default_factory=dict)
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    missing_field_counts: dict[str, int] = Field(default_factory=dict)
    unmapped_field_counts: dict[str, int] = Field(default_factory=dict)
    entity_kind_counts: dict[str, int] = Field(default_factory=dict)
    missing_entity_kind_counts: dict[str, int] = Field(default_factory=dict)
    warning_counts: dict[str, int] = Field(default_factory=dict)
    suspicious_samples: list[NormalizationDriftSample] = Field(default_factory=list)
    samples: list[NormalizationDriftSample] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)
    knowledge_candidates: list[str] = Field(default_factory=list)

    @field_validator("evidence")
    @classmethod
    def require_evidence(cls, evidence: list[EvidenceItem]) -> list[EvidenceItem]:
        if not evidence:
            raise ValueError("analysis result must include at least one evidence item")
        return evidence


class Decision(BaseModel):
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_action: str
    needs_review: bool
    reason: str
    automation_allowed: Literal[False] = False


class CorrectionCommand(BaseModel):
    run_id: str
    corrected_verdict: Verdict
    reason: str = Field(min_length=1)
    corrected_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class CorrectionRecord(BaseModel):
    correction_id: str = Field(default_factory=lambda: f"COR-{uuid4().hex[:12].upper()}")
    run_id: str
    previous_verdict: Verdict | None = None
    corrected_verdict: Verdict
    reason: str
    corrected_confidence: float | None = None
    actor: ActorContext
    created_at: datetime = Field(default_factory=utc_now)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    candidate_knowledge_status: Literal["not_created", "pending_review"] = "not_created"


class DecisionAuditRecord(BaseModel):
    audit_id: str = Field(default_factory=lambda: f"AUD-{uuid4().hex[:12].upper()}")
    action: AuditAction
    run_id: str
    alert_id: str
    actor: ActorContext
    occurred_at: datetime = Field(default_factory=utc_now)
    input_hash: str | None = None
    previous_verdict: Verdict | None = None
    final_verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    replay_of_run_id: str | None = None
    correction_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AlertSummary(BaseModel):
    """Queryable read model for alert queues, dedup, and review surfaces.

    ``AnalysisRun`` remains the full source of truth. This model intentionally
    keeps only indexed/list-friendly fields that UI, TUI, daemon, and future
    correlation steps need to scan cheaply.
    """

    schema_version: str = "soc.alert_summary.v1"
    run_id: str
    alert_id: str
    tenant_id: str | None = None
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    detection_key: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    severity: str | None = None
    category: str | None = None
    entity_keys: list[str] = Field(default_factory=list)
    status: AnalysisRunStatus
    verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = False
    summary: str | None = None
    recommended_action: str | None = None
    input_hash: str | None = None
    replay_of_run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SimilarAlertQuery(BaseModel):
    """Candidate retrieval query derived from one alert summary."""

    run_id: str
    detection_key: str | None = None
    rule_code: str | None = None
    source_type: AlertSourceType | None = None
    category: str | None = None
    entity_keys: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=100)
    candidate_limit: int = Field(default=200, ge=1, le=1000)


class SimilarAlertMatch(BaseModel):
    """Scored historical alert summary match."""

    summary: AlertSummary
    score: float = Field(ge=0.0)
    matched_reasons: list[str] = Field(default_factory=list)


class CorrelationQuery(BaseModel):
    """Request to correlate one alert summary with recent historical alerts."""

    run_id: str
    limit: int = Field(default=10, ge=1, le=100)
    candidate_limit: int = Field(default=200, ge=1, le=1000)
    evidence_limit_per_match: int = Field(default=5, ge=0, le=50)


class CorrelationEvidenceRef(BaseModel):
    """Reusable investigation evidence attached to a correlated historical alert."""

    evidence_id: str
    route: str
    action: str
    status: Literal["success", "denied", "failed"]
    message: str
    result_payload: dict[str, Any] = Field(default_factory=dict)
    queue_id: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    source_proposal_id: str | None = None
    created_at: datetime


class CorrelationMatch(BaseModel):
    """One correlated historical alert plus evidence that can be reused in review."""

    summary: AlertSummary
    score: float = Field(ge=0.0)
    matched_reasons: list[str] = Field(default_factory=list)
    reusable_evidence: list[CorrelationEvidenceRef] = Field(default_factory=list)


class CorrelationResult(BaseModel):
    """Deterministic correlation result for CLI, TUI, Web, and review context."""

    schema_version: str = "soc.correlation_result.v1"
    query: CorrelationQuery
    subject_summary: AlertSummary
    matches: list[CorrelationMatch] = Field(default_factory=list)
    reusable_evidence_count: int = 0
    generated_at: datetime = Field(default_factory=utc_now)


class ReviewQueueItem(BaseModel):
    """Human review queue item derived from an alert summary."""

    schema_version: str = "soc.review_queue.v1"
    queue_id: str = Field(default_factory=lambda: f"REV-{uuid4().hex[:12].upper()}")
    run_id: str
    alert_id: str
    tenant_id: str | None = None
    status: ReviewQueueStatus = ReviewQueueStatus.OPEN
    priority: ReviewQueuePriority = ReviewQueuePriority.MEDIUM
    reason: str
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    severity: str | None = None
    category: str | None = None
    verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    entity_keys: list[str] = Field(default_factory=list)
    summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None
    closed_by: ActorContext | None = None
    close_reason: str | None = None


class ReviewQueueCloseCommand(BaseModel):
    queue_id: str
    reason: str = Field(min_length=1)


class PipelineStepTrace(BaseModel):
    step_name: str
    status: PipelineStepStatus
    input_hash: str | None = None
    output_hash: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisNodeOutput(BaseModel):
    """Auditable output returned by a bounded SOC analysis node."""

    analysis: AnalysisResult
    model_name: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    parser_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"RUN-{uuid4().hex[:12].upper()}")
    alert_id: str
    status: AnalysisRunStatus
    pipeline_version: str = "phase1-runtime-v0"
    model_name: str = "stub"
    prompt_version: str = "stub"
    input_payload: dict[str, Any] | None = None
    input_hash: str | None = None
    replay_of_run_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    steps: list[PipelineStepTrace] = Field(default_factory=list)
    entities: ExtractedEntities | None = None
    normalization_report: NormalizationReport | None = None
    extraction_report: ExtractionReport | None = None
    fact_reconstruction: FactReconstructionResult | None = None
    llm_analysis_request: LLMAnalysisRequest | None = None
    analysis: AnalysisResult | None = None
    decision: Decision | None = None
    corrections: list[CorrectionRecord] = Field(default_factory=list)


class SocDomainTriageRequest(BaseModel):
    """Input contract for one bounded SOC domain handler."""

    schema_version: str = "soc.domain_triage_request.v1"
    request_id: str = Field(default_factory=lambda: f"DTR-{uuid4().hex[:12].upper()}")
    run: AnalysisRun
    domain: SocDomainName | None = None
    skill_context: SocSkillContext = Field(default_factory=SocSkillContext)
    investigation_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    capability_card_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocDomainFinding(BaseModel):
    """One bounded domain finding; it is not an operational verdict."""

    schema_version: str = "soc.domain_finding.v1"
    finding_id: str = Field(default_factory=lambda: f"DFN-{uuid4().hex[:12].upper()}")
    domain: SocDomainName
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    severity: SocDomainFindingSeverity = SocDomainFindingSeverity.MEDIUM
    disposition: SocDomainFindingDisposition = SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    capability_card_refs: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocDomainTriageResult(BaseModel):
    """Output from one SOC domain handler."""

    schema_version: str = "soc.domain_triage_result.v1"
    request_id: str
    run_id: str
    alert_id: str
    domain: SocDomainName
    handler_id: str = Field(min_length=1)
    findings: list[SocDomainFinding] = Field(default_factory=list)
    evidence_ref_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocOrchestratorActionSpec(BaseModel):
    """One explicit read-only action requested by the main orchestrator."""

    route: str = Field(min_length=1)
    action: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SocOrchestratorRouteStep(BaseModel):
    """One route/action/evidence step inside a main orchestrator report."""

    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status: Literal["success", "denied", "failed"]
    message: str = Field(min_length=1)
    evidence_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SocOrchestratorReviewContextSummary(BaseModel):
    """Bounded review context summary for analyst-facing reports."""

    run_id: str
    alert_id: str
    verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = True
    reason: str | None = None
    analysis_summary: str | None = None
    action_evidence_count: int = Field(default=0, ge=0)
    domain_finding_count: int = Field(default=0, ge=0)


class SocMainOrchestratorRequest(BaseModel):
    """Input for one bounded main-orchestrator demo run."""

    schema_version: str = "soc.main_orchestrator_request.v1"
    payload: dict[str, Any]
    sample_id: str | None = None
    thread_id: str | None = None
    action_specs: list[SocOrchestratorActionSpec] = Field(default_factory=list)
    capability_card_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnifiedInvestigationReport(BaseModel):
    """Unified report assembled by the SOC main orchestrator."""

    schema_version: str = "soc.unified_investigation_report.v1"
    report_id: str = Field(default_factory=lambda: f"UIR-{uuid4().hex[:12].upper()}")
    sample_id: str | None = None
    run: AnalysisRun
    skill_context: SocSkillContext = Field(default_factory=SocSkillContext)
    route_steps: list[SocOrchestratorRouteStep] = Field(default_factory=list)
    investigation_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    domain_triage_results: list[SocDomainTriageResult] = Field(default_factory=list)
    review_context: SocOrchestratorReviewContextSummary
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvestigationTimelineItem(BaseModel):
    """One analyst-facing event in the unified investigation timeline."""

    schema_version: str = "soc.investigation_timeline_item.v1"
    item_id: str = Field(default_factory=lambda: f"TIM-{uuid4().hex[:12].upper()}")
    kind: Literal[
        "analysis",
        "decision",
        "correlation",
        "domain_finding",
        "read_only_evidence",
        "external_disposition",
        "memory_candidate",
        "relevant_memory",
        "audit",
        "correction",
    ]
    title: str = Field(min_length=1)
    summary: str | None = None
    status: str | None = None
    severity: str | None = None
    source_id: str | None = None
    source_refs: dict[str, str] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class UnifiedInvestigationView(BaseModel):
    """Read-only analyst view assembled from existing SOC investigation products."""

    schema_version: str = "soc.unified_investigation_view.v1"
    view_id: str = Field(default_factory=lambda: f"UIV-{uuid4().hex[:12].upper()}")
    queue_id: str
    run_id: str
    alert_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    runtime_verdict: Verdict | None = None
    runtime_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = True
    automation_allowed: bool = False
    primary_summary: str | None = None
    primary_reason: str | None = None
    correlation_result: CorrelationResult | None = None
    domain_triage_results: list[SocDomainTriageResult] = Field(default_factory=list)
    evidence_timeline: list[InvestigationTimelineItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    boundary_notes: list[str] = Field(
        default_factory=lambda: [
            "This view is read-only analyst context.",
            "Domain findings and relevant memories do not change the operational verdict.",
            "External feedback and memory updates must still pass service boundaries.",
        ]
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvestigationContext(BaseModel):
    """Read model used by analyst surfaces to open one review item."""

    schema_version: str = "soc.investigation_context.v1"
    queue_item: ReviewQueueItem
    run: AnalysisRun
    summary: AlertSummary | None = None
    audit_records: list[DecisionAuditRecord] = Field(default_factory=list)
    similar_alerts: list[SimilarAlertMatch] = Field(default_factory=list)
    action_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    external_dispositions: list[SocExternalDispositionRecord] = Field(default_factory=list)
    memory_candidates: list[SocMemoryCandidate] = Field(default_factory=list)
    relevant_memories: SocMemoryRetrievalResult | None = None
    correlation_result: CorrelationResult | None = None
    domain_triage_results: list[SocDomainTriageResult] = Field(default_factory=list)
    investigation_view: UnifiedInvestigationView | None = None
