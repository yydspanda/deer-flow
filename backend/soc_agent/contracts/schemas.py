"""Pydantic contracts for Phase 1 SOC Agent runtime boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    REVIEW_CORRECTED = "review.corrected"
    REVIEW_REQUESTED = "review.requested"
    MEMORY_UPDATED = "memory.updated"


class AuditAction(StrEnum):
    ANALYSIS = "analysis"
    REPLAY = "replay"
    CORRECTION = "correction"


class ReviewQueueStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ReviewQueuePriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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


class InvestigationContext(BaseModel):
    """Read model used by analyst surfaces to open one review item."""

    schema_version: str = "soc.investigation_context.v1"
    queue_item: ReviewQueueItem
    run: AnalysisRun
    summary: AlertSummary | None = None
    audit_records: list[DecisionAuditRecord] = Field(default_factory=list)
    similar_alerts: list[SimilarAlertMatch] = Field(default_factory=list)
