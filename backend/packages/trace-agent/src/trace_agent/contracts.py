"""Shared wire contracts from the reviewed ZEUS/AI/Trace handoff agreement.

These models validate representations, not investigation quality or execution
readiness. Durable identity, idempotence, revisions and fencing belong to the
real engine implementing :class:`trace_agent.engine.TraceEngine`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_EVIDENCE_GAPS_CHARS = 32_000


def validate_task_id(value: str) -> str:
    """Validate an opaque upstream id without rewriting it or making paths."""
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("Invalid task_id")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value) or any(fragment in value for fragment in ("/", "\\", "..")):
        raise ValueError("Unsafe task_id")
    return value


def _alert_id(value: Any) -> str:
    # Source JSON ids can be integer values; booleans/floats are not ids.
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError("Missing or invalid alertId")
    return value


def extract_alert_id(payload: Mapping[str, Any]) -> str:
    """Bind bare or enveloped alert identity, refusing conflicting id claims."""
    if not isinstance(payload, Mapping):
        raise ValueError("Alert JSON must be an object")
    values = []
    if "alertId" in payload:
        values.append(_alert_id(payload["alertId"]))
    if "alert" in payload:
        inner = payload["alert"]
        if not isinstance(inner, Mapping):
            raise ValueError("Alert envelope must contain an object")
        if "alertId" not in inner:
            raise ValueError("Missing alert.alertId")
        values.append(_alert_id(inner["alertId"]))
    if not values or any(value != values[0] for value in values[1:]):
        raise ValueError("Missing or conflicting alert identity")
    return values[0]


def normalize_evidence_gaps(value: str | None) -> list[str]:
    """Trim lines and remove blanks, preserving order and repeated questions."""
    if value is None:
        return []
    if not isinstance(value, str) or len(value) > MAX_EVIDENCE_GAPS_CHARS:
        raise ValueError("evidence_gaps exceeds its character limit")
    return [line.strip() for line in value.splitlines() if line.strip()]


class TraceStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TraceVerdict(StrEnum):
    CONFIRMED_RISK = "风险属实"
    NORMAL_BEHAVIOR = "正常行为"
    DETECTION_FALSE_POSITIVE = "检测误报"
    NEEDS_VERIFICATION = "待核实"


class TraceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TraceCreateResponse(TraceModel):
    taskId: str
    status: TraceStatus

    _task_id = field_validator("taskId")(validate_task_id)


class TraceResult(TraceModel):
    alert_id: str
    report: str | None = None
    verdict: TraceVerdict | None = None
    verdict_reason: str | None = Field(default=None, max_length=1000)
    riskLevel: str | None = None
    completeness: float | None = Field(default=None, ge=0, le=1, strict=True)
    confidence: float | None = Field(default=None, ge=0, le=1, strict=True)

    @field_validator("alert_id")
    @classmethod
    def nonempty_identity(cls, value: str) -> str:
        return _alert_id(value)


class TraceTaskResponse(TraceCreateResponse):
    result: TraceResult
    errorCode: str | None = None
    errorMessage: str | None = None

    @model_validator(mode="after")
    def valid_state_result(self) -> Self:
        fields = ("report", "verdict", "verdict_reason", "riskLevel", "completeness", "confidence")
        if self.status == TraceStatus.SUCCESS:
            if any(getattr(self.result, field) is None for field in fields):
                raise ValueError("SUCCESS requires a complete formal result")
            if not self.result.report.strip() or not self.result.verdict_reason.strip():
                raise ValueError("SUCCESS requires a nonempty report and reason")
        elif any(getattr(self.result, field) is not None for field in fields):
            raise ValueError("Non-success results must contain only alert_id")
        return self


# A complete GET representation; operation acknowledgements use TraceCreateResponse.
TraceSnapshot = TraceTaskResponse


class TraceHealth(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["ok", "degraded"]
    detail: str | None = None


class TraceOperation(TraceModel):
    operation_id: str = Field(min_length=1, max_length=256)
    expected_version: int = Field(ge=1, strict=True)

    @field_validator("operation_id")
    @classmethod
    def nonempty_operation_id(cls, value: str) -> str:
        if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Invalid operation id")
        return value


class TraceCreateRequest(TraceModel):
    """Engine input: original alert object and already normalized question lines."""

    task_id: str
    alert: dict[str, Any]
    alert_id: str
    evidence_gaps: list[str] = Field(default_factory=list)
    operation_id: str = Field(min_length=1, max_length=256)

    _task_id = field_validator("task_id")(validate_task_id)

    @model_validator(mode="after")
    def matching_identity(self) -> Self:
        if extract_alert_id(self.alert) != self.alert_id:
            raise ValueError("Alert identity does not match the submission")
        TraceOperation(operation_id=self.operation_id, expected_version=1)
        if any(not value or value != value.strip() or value.splitlines() != [value] for value in self.evidence_gaps):
            raise ValueError("evidence_gaps must contain normalized, nonempty question lines")
        if len("\n".join(self.evidence_gaps)) > MAX_EVIDENCE_GAPS_CHARS:
            raise ValueError("evidence_gaps exceeds its character limit")
        return self
