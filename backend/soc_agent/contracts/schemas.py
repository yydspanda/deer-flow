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


class AlertInput(BaseModel):
    """Normalized alert input accepted by the Phase 1 CLI/runtime."""

    model_config = ConfigDict(extra="allow")

    alert_id: str = Field(default_factory=lambda: f"ALT-{uuid4().hex[:12].upper()}")
    rule_name: str | None = None
    source_ip: str | None = None
    destination_ip: str | None = None
    dst_port: int | None = None
    domain: str | None = None
    process_name: str | None = None
    command_line: str | None = None
    username: str | None = None
    host_name: str | None = None
    severity: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_common_alert_id_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "alert_id" not in normalized:
            for key in ("id", "event_id", "alertId"):
                if key in normalized and normalized[key]:
                    normalized["alert_id"] = str(normalized[key])
                    break
        normalized.setdefault("raw", dict(data))
        return normalized


class ExtractedEntities(BaseModel):
    ips: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    processes: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    source: str
    description: str
    value: str | int | float | bool | None = None


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
