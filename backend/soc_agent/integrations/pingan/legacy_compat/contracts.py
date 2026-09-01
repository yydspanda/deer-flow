"""Wire contracts retained for the legacy ZEUS task integration."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from soc_agent.contracts import ProcessingJobStatus

PINGAN_LEGACY_QUEUE_NAME = "deepseek-v4-flash"
PINGAN_LEGACY_MODEL_NAME = "deepseek-v4-flash-0731"

_PROFILE_PRIORITIES = {
    "RPAADM_002635": 9,
    "RPAADM_002638": 9,
    "RPAADM_002524": 8,
    "RPAADM_002627": 8,
    "RPAADM_002558": 8,
    "RPAADM_002624": 7,
    "RPAADM_002574": 7,
    "RPAADM_002528": 6,
    "RPAADM_002531": 5,
    "RPAADM_002679": 5,
    "RPAADM_002631": 3,
}


class PingAnLegacyTaskRequest(BaseModel):
    """Exact externally visible fields accepted by old `/workflow/task`."""

    model_config = ConfigDict(extra="ignore")

    app_code: str = Field(min_length=1, max_length=64)
    flow_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=128)
    alert_id: str = Field(min_length=1, max_length=128)
    alert_data: dict[str, Any] | None = None

    @field_validator("app_code", "flow_id", "session_id", "alert_id")
    @classmethod
    def _normalize_identifier(cls, value: str) -> str:
        return value.strip()


class PingAnLegacyTaskResponse(BaseModel):
    """Legacy Celery-shaped response retained without Celery semantics."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["PENDING", "STARTED", "RETRY", "SUCCESS", "FAILURE"]
    result: Any = None


class PingAnLegacyTaskMetadata(BaseModel):
    """PingAn-only projection used to populate generic indexed job fields."""

    model_config = ConfigDict(extra="forbid")

    app_code: str
    flow_id: str
    session_id: str
    execute_type: str | None = None
    profile_code: str | None = None
    rule_code: str | None = None
    detection_key: str | None = None
    priority: int = Field(ge=0, le=9)
    queue_name: Literal["deepseek-v4-flash"] = PINGAN_LEGACY_QUEUE_NAME
    model_name: Literal["deepseek-v4-flash-0731"] = PINGAN_LEGACY_MODEL_NAME


class PingAnAlertLifecycleState(StrEnum):
    PENDING = "pending"
    HANDLED = "handled"
    UNKNOWN = "unknown"


class PingAnAlertLifecycleCheck(BaseModel):
    """Bounded result of the pre-analysis ZEUS lifecycle query."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.pingan_alert_lifecycle_check.v1"
    alert_id: str
    state: PingAnAlertLifecycleState
    provider_status: str | None = None
    reason: str | None = None
    mocked: bool
    response_sha256: str | None = None


def extract_pingan_legacy_task_metadata(
    request: PingAnLegacyTaskRequest,
) -> PingAnLegacyTaskMetadata:
    alert = request.alert_data.get("alert") if isinstance(request.alert_data, dict) else None
    alert = alert if isinstance(alert, dict) else {}
    execute_type = _optional_text(alert.get("executeType"))
    profile_code = _optional_text(alert.get("profileCode"))
    rule_code = _first_text(
        alert,
        "ruleCode",
        "rule_code",
        "ruleId",
        "rule_id",
    )
    numeric_execute_type = _optional_int(execute_type)
    priority = _PROFILE_PRIORITIES.get(profile_code or "", 9) if numeric_execute_type in {1, 3} else 0
    return PingAnLegacyTaskMetadata(
        app_code=request.app_code,
        flow_id=request.flow_id,
        session_id=request.session_id,
        execute_type=execute_type,
        profile_code=profile_code,
        rule_code=rule_code,
        detection_key=f"rule_code:{rule_code}" if rule_code else None,
        priority=priority,
    )


def project_legacy_task_status(status: ProcessingJobStatus) -> str:
    if status is ProcessingJobStatus.QUEUED:
        return "PENDING"
    if status in {
        ProcessingJobStatus.CLAIMED,
        ProcessingJobStatus.PRECHECKING,
        ProcessingJobStatus.ANALYZING,
        ProcessingJobStatus.PROJECTING,
    }:
        return "STARTED"
    if status is ProcessingJobStatus.FAILED:
        return "FAILURE"
    return "SUCCESS"


def _first_text(values: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _optional_text(values.get(key))
        if value:
            return value
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


__all__ = [
    "PINGAN_LEGACY_MODEL_NAME",
    "PINGAN_LEGACY_QUEUE_NAME",
    "PingAnAlertLifecycleCheck",
    "PingAnAlertLifecycleState",
    "PingAnLegacyTaskMetadata",
    "PingAnLegacyTaskRequest",
    "PingAnLegacyTaskResponse",
    "extract_pingan_legacy_task_metadata",
    "project_legacy_task_status",
]
