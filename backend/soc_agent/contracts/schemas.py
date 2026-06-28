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


class ExtractedEntities(BaseModel):
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


class AnalysisRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"RUN-{uuid4().hex[:12].upper()}")
    alert_id: str
    status: AnalysisRunStatus
    pipeline_version: str = "phase1-runtime-v0"
    model_name: str = "stub"
    prompt_version: str = "stub"
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    steps: list[PipelineStepTrace] = Field(default_factory=list)
    entities: ExtractedEntities | None = None
    analysis: AnalysisResult | None = None
    decision: Decision | None = None
