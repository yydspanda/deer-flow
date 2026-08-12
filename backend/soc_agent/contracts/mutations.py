"""Vendor-neutral audit contracts for SOC state mutations."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .common import ActorContext


class SocMutationOperation(StrEnum):
    REVIEW_CORRECT = "review.correct"
    REVIEW_ROLE_CONFIRM = "review.role_confirm"
    REVIEW_CLOSE = "review.close"
    REVIEW_NOTE = "review.note"
    MEMORY_REVIEW = "memory.review"
    MEMORY_RETRIEVAL_ACTIVATION = "memory.retrieval_activation"
    MEMORY_PATTERN_OBSERVATION_INGEST = "memory_pattern_observation.ingest"
    APPROVAL_REQUEST_SUBMIT = "approval.request.submit"
    APPROVAL_REQUEST_APPROVE = "approval.request.approve"
    APPROVAL_REQUEST_REJECT = "approval.request.reject"
    APPROVAL_REQUEST_EXPIRE = "approval.request.expire"
    APPROVAL_ACTION_DRY_RUN = "approval.action.dry_run"
    APPROVAL_ACTION_EXECUTE = "approval.action.execute"
    EXTERNAL_DISPOSITION_APPLY = "external_disposition.apply"
    SKILL_FEEDBACK_INGEST = "skill_feedback.ingest"
    SKILL_IMPROVEMENT_REVIEW = "skill_improvement.review"


class SocMutationAuditRecord(BaseModel):
    """Append-only, bounded audit record for one service-level mutation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.mutation_audit.v1"
    audit_id: str = Field(default_factory=lambda: f"MAUD-{uuid4().hex[:12].upper()}")
    operation: SocMutationOperation
    target_type: str = Field(min_length=1, max_length=64)
    target_id: str = Field(min_length=1, max_length=256)
    run_id: str | None = Field(default=None, max_length=64)
    alert_id: str | None = Field(default=None, max_length=128)
    queue_id: str | None = Field(default=None, max_length=64)
    actor: ActorContext
    request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=512)
    command_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)
    result_status: str = Field(default="succeeded", min_length=1, max_length=64)
    result_ref: str | None = Field(default=None, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = ["SocMutationAuditRecord", "SocMutationOperation"]
