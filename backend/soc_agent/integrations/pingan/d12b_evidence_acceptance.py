"""D12-B acceptance for MCP dispatch, evidence persistence, and readback."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.context_bridge import build_lead_agent_review_context_artifact
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AnalysisRun,
    EntrySurface,
    InvestigationEvidence,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocAgentChatRequest,
)
from soc_agent.core.service import (
    SocAgentActionDispatcher,
    SocAgentCapabilityRouter,
    SocReviewService,
)
from soc_agent.integrations.pingan.d12b_acceptance import (
    PingAnAssetCaseKind,
    PingAnAssetCaseMatrix,
    PingAnAssetCaseSpec,
    build_pingan_asset_case_matrix_plan,
)
from soc_agent.protocols import SocActionAdapterRegistryPort
from soc_agent.utils.hashing import stable_hash

_ACTION = "asset.locate"
_DEFAULT_ACTOR_ID = "d12b-internal-validator"
_MAX_EVIDENCE_READBACK = 100


class PingAnD12BEvidenceAcceptanceStatus(StrEnum):
    """Overall acceptance status for one real MCP evidence path."""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class PingAnD12BEvidenceCheckStatus(StrEnum):
    """Status of one bounded acceptance assertion."""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class PingAnD12BEvidenceCheck(BaseModel):
    """One value-free D12-B evidence-path assertion."""

    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    status: PingAnD12BEvidenceCheckStatus
    detail_code: str = Field(pattern=r"^[a-z0-9][a-z0-9._:-]{1,255}$")


class PingAnD12BEvidenceAcceptanceReport(BaseModel):
    """Bounded D12-B report that never contains raw lookup values or results."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_d12b_evidence_acceptance.v1"] = "soc.pingan_d12b_evidence_acceptance.v1"
    status: PingAnD12BEvidenceAcceptanceStatus
    matrix_id: str
    matrix_plan_hash: str = Field(min_length=64, max_length=64)
    case_id: str
    case_kind: PingAnAssetCaseKind | None = None
    query_hash: str | None = Field(default=None, min_length=64, max_length=64)
    queue_id: str
    run_id: str | None = None
    alert_id: str | None = None
    thread_id: str
    route: Literal["asset.locate"] = _ACTION
    action: Literal["asset.locate"] = _ACTION
    action_dispatch_attempted: bool = False
    action_status: Literal["success", "denied", "failed"] | None = None
    evidence_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    provider_mode: str | None = None
    mocked_observed: bool | None = None
    evidence_boundary: str | None = None
    decision_impact: str | None = None
    raw_response_included: bool | None = None
    evidence_persisted: bool = False
    review_context_visible: bool = False
    lead_agent_context_visible: bool = False
    run_state_hash_before: str | None = Field(default=None, min_length=64, max_length=64)
    run_state_hash_after: str | None = Field(default=None, min_length=64, max_length=64)
    review_state_hash_before: str | None = Field(default=None, min_length=64, max_length=64)
    review_state_hash_after: str | None = Field(default=None, min_length=64, max_length=64)
    run_state_unchanged: bool = False
    review_state_unchanged: bool = False
    checks: list[PingAnD12BEvidenceCheck] = Field(default_factory=list)
    error_type: str | None = Field(default=None, max_length=256)
    contains_raw_query: Literal[False] = False
    contains_raw_um: Literal[False] = False
    contains_raw_provider_response: Literal[False] = False
    web_or_tui_render_executed: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PingAnD12BEvidenceRepositoryPort(Protocol):
    """Small repository surface needed by the D12-B acceptance executor."""

    def get_run(self, run_id: str) -> AnalysisRun | None: ...

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None: ...

    def save_evidence(self, evidence: InvestigationEvidence) -> None: ...

    def list_evidence(
        self,
        *,
        queue_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> list[InvestigationEvidence]: ...


def run_pingan_d12b_evidence_acceptance(
    matrix: PingAnAssetCaseMatrix,
    *,
    case_id: str,
    queue_id: str,
    action_adapter_registry: SocActionAdapterRegistryPort,
    repository: PingAnD12BEvidenceRepositoryPort,
    thread_id: str | None = None,
    request_context: ServiceRequestContext | None = None,
) -> PingAnD12BEvidenceAcceptanceReport:
    """Exercise the production-shaped MCP/action/evidence/readback path once."""

    resolved_thread_id = thread_id or f"D12B-EVIDENCE-{queue_id}"
    base = _report_base(
        matrix,
        case_id=case_id,
        queue_id=queue_id,
        thread_id=resolved_thread_id,
    )
    case = next((item for item in matrix.cases if item.case_id == case_id), None)
    if case is None:
        return _blocked_report(base, check_id="precondition.case", detail_code="case_not_found")
    base.update(
        case_kind=case.kind,
        query_hash=_query_hash(case.query),
    )
    precondition = _case_block_reason(case)
    if precondition is not None:
        return _blocked_report(base, check_id="precondition.case", detail_code=precondition)

    try:
        review_item = repository.get_review_item(queue_id)
    except Exception as exc:  # noqa: BLE001 - report exposes only the exception type
        return _blocked_report(
            base,
            check_id="precondition.review_repository",
            detail_code="review_repository_unavailable",
            error_type=exc.__class__.__name__,
        )
    if review_item is None:
        return _blocked_report(
            base,
            check_id="precondition.review_item",
            detail_code="review_item_not_found",
        )
    base.update(run_id=review_item.run_id, alert_id=review_item.alert_id)
    if review_item.status is not ReviewQueueStatus.OPEN:
        return _blocked_report(
            base,
            check_id="precondition.review_item",
            detail_code="review_item_not_open",
        )

    try:
        run = repository.get_run(review_item.run_id)
    except Exception as exc:  # noqa: BLE001 - report exposes only the exception type
        return _blocked_report(
            base,
            check_id="precondition.run_repository",
            detail_code="run_repository_unavailable",
            error_type=exc.__class__.__name__,
        )
    if run is None:
        return _blocked_report(
            base,
            check_id="precondition.run",
            detail_code="analysis_run_not_found",
        )
    if run.alert_id != review_item.alert_id:
        return _blocked_report(
            base,
            check_id="precondition.references",
            detail_code="run_review_alert_mismatch",
        )

    run_hash_before = _model_hash(run)
    review_hash_before = _model_hash(review_item)
    base.update(
        run_state_hash_before=run_hash_before,
        review_state_hash_before=review_hash_before,
    )
    context = request_context or _default_request_context(
        case_id=case.case_id,
        queue_id=review_item.queue_id,
    )
    base.update(request_id=context.request_id, trace_id=context.trace_id)
    request = _action_request(
        case,
        review_item=review_item,
        thread_id=resolved_thread_id,
    )
    router = SocAgentCapabilityRouter(allowed_routes={_ACTION})
    dispatcher = SocAgentActionDispatcher(
        action_adapter_registry=action_adapter_registry,
        evidence_repository=repository,
    )
    route = router.route(request)
    if not route.allowed:
        return _blocked_report(
            base,
            check_id="dispatch.route",
            detail_code="asset_locate_route_denied",
        )
    permission = dispatcher.check_permission(request, route, context=context)
    if not permission.allowed:
        return _blocked_report(
            base,
            check_id="dispatch.permission",
            detail_code="asset_locate_permission_denied",
        )

    base["action_dispatch_attempted"] = True
    try:
        result = dispatcher.dispatch(
            request,
            route,
            context=context,
            permission_decision=permission,
        )
    except Exception as exc:  # noqa: BLE001 - report exposes only the exception type
        return _failed_after_dispatch(
            base,
            repository=repository,
            run_id=run.run_id,
            queue_id=review_item.queue_id,
            check_id="dispatch.execution",
            detail_code="action_dispatch_raised",
            error_type=exc.__class__.__name__,
        )
    base["action_status"] = result.status
    if result.status != "success":
        return _blocked_after_dispatch(
            base,
            repository=repository,
            run_id=run.run_id,
            queue_id=review_item.queue_id,
            check_id="dispatch.execution",
            detail_code="action_result_not_success",
            error_type=_bounded_error_type(result.payload),
        )

    evidence_id = result.payload.get("evidence_id")
    base["evidence_id"] = evidence_id if isinstance(evidence_id, str) else None
    mcp_result = result.payload.get("mcp_result")
    bounded_result = mcp_result if isinstance(mcp_result, Mapping) else {}
    base.update(
        provider_mode=_optional_string(bounded_result.get("provider_mode")),
        mocked_observed=_optional_bool(bounded_result.get("mocked")),
        evidence_boundary=_optional_string(bounded_result.get("evidence_boundary")),
        decision_impact=_optional_string(bounded_result.get("decision_impact")),
        raw_response_included=_optional_bool(bounded_result.get("raw_response_included")),
    )

    try:
        after_run = repository.get_run(run.run_id)
        after_review = repository.get_review_item(review_item.queue_id)
        evidence_items = repository.list_evidence(
            thread_id=resolved_thread_id,
            limit=_MAX_EVIDENCE_READBACK,
        )
        persisted = next(
            (item for item in evidence_items if base["evidence_id"] is not None and item.evidence_id == base["evidence_id"]),
            None,
        )
        review_context = SocReviewService(
            repository=repository,
            review_queue_repository=repository,
            evidence_repository=repository,
        ).get_investigation_context(review_item.queue_id)
        artifact = build_lead_agent_review_context_artifact(
            review_context,
            request_context=context,
        )
    except Exception as exc:  # noqa: BLE001 - report exposes only the exception type
        return _failed_after_dispatch(
            base,
            repository=repository,
            run_id=run.run_id,
            queue_id=review_item.queue_id,
            check_id="readback.execution",
            detail_code="evidence_readback_raised",
            error_type=exc.__class__.__name__,
        )

    run_hash_after = _model_hash(after_run) if after_run is not None else None
    review_hash_after = _model_hash(after_review) if after_review is not None else None
    review_visible = _evidence_visible(
        review_context.action_evidence,
        evidence_id=base["evidence_id"],
    )
    lead_visible = any(item.get("evidence_id") == base["evidence_id"] for item in artifact.action_evidence if isinstance(item, Mapping))
    evidence_contract_valid = _evidence_contract_valid(
        persisted,
        queue_item=review_item,
        thread_id=resolved_thread_id,
    )
    evidence_provenance_valid = bool(persisted is not None and persisted.request_id == context.request_id and bool(context.trace_id) and persisted.trace_id == context.trace_id)
    result_is_real_internal = bounded_result.get("provider_mode") == "internal" and bounded_result.get("mocked") is False
    evidence_is_real = persisted is not None and persisted.mocked is False
    investigation_only = result.payload.get("read_only") is True and result.payload.get("external_side_effect") == "read" and bounded_result.get("evidence_boundary") == "investigation_only"
    decision_impact_none = bounded_result.get("decision_impact") == "none"
    raw_response_excluded = bounded_result.get("raw_response_included") is False
    run_unchanged = run_hash_after == run_hash_before
    review_unchanged = review_hash_after == review_hash_before
    checks = [
        _assertion("dispatch.route", route.allowed, "asset_locate_route_allowed"),
        _assertion(
            "dispatch.permission",
            permission.allowed,
            "asset_locate_read_only_permission_allowed",
        ),
        _assertion("dispatch.execution", True, "asset_locate_action_succeeded"),
        _assertion(
            "provider.real_internal",
            result_is_real_internal,
            "real_internal_provider_observed",
        ),
        _assertion(
            "provider.investigation_only",
            investigation_only,
            "investigation_only_boundary_observed",
        ),
        _assertion(
            "provider.decision_impact",
            decision_impact_none,
            "decision_impact_none_observed",
        ),
        _assertion(
            "provider.raw_response",
            raw_response_excluded,
            "raw_provider_response_excluded",
        ),
        _assertion(
            "evidence.persisted",
            persisted is not None,
            "investigation_evidence_persisted",
        ),
        _assertion(
            "evidence.contract",
            evidence_contract_valid,
            "investigation_evidence_references_valid",
        ),
        _assertion(
            "evidence.provenance",
            evidence_provenance_valid,
            "investigation_evidence_request_trace_valid",
        ),
        _assertion(
            "evidence.real",
            evidence_is_real,
            "persisted_evidence_not_mocked",
        ),
        _assertion(
            "readback.review_context",
            review_visible,
            "shared_review_context_contains_evidence",
        ),
        _assertion(
            "readback.lead_agent",
            lead_visible,
            "lead_agent_context_contains_evidence",
        ),
        _assertion(
            "side_effect.run_state",
            run_unchanged,
            "analysis_run_unchanged",
        ),
        _assertion(
            "side_effect.review_state",
            review_unchanged,
            "review_queue_item_unchanged",
        ),
    ]
    passed = all(item.status is PingAnD12BEvidenceCheckStatus.PASSED for item in checks)
    return PingAnD12BEvidenceAcceptanceReport(
        **base,
        status=(PingAnD12BEvidenceAcceptanceStatus.PASSED if passed else PingAnD12BEvidenceAcceptanceStatus.FAILED),
        evidence_persisted=persisted is not None,
        review_context_visible=review_visible,
        lead_agent_context_visible=lead_visible,
        run_state_hash_after=run_hash_after,
        review_state_hash_after=review_hash_after,
        run_state_unchanged=run_unchanged,
        review_state_unchanged=review_unchanged,
        checks=checks,
    )


def _report_base(
    matrix: PingAnAssetCaseMatrix,
    *,
    case_id: str,
    queue_id: str,
    thread_id: str,
) -> dict[str, object]:
    plan = build_pingan_asset_case_matrix_plan(matrix)
    return {
        "matrix_id": matrix.matrix_id,
        "matrix_plan_hash": stable_hash(plan.model_dump(mode="json")),
        "case_id": case_id,
        "queue_id": queue_id,
        "thread_id": thread_id,
    }


def _case_block_reason(case: PingAnAssetCaseSpec) -> str | None:
    if case.expected_outcome != "found":
        return "case_expected_outcome_not_found"
    if case.environment_overrides:
        return "case_environment_overrides_not_supported"
    if _looks_like_placeholder(case.query) or (case.um and _looks_like_placeholder(case.um)):
        return "case_contains_placeholder"
    return None


def _action_request(
    case: PingAnAssetCaseSpec,
    *,
    review_item: ReviewQueueItem,
    thread_id: str,
) -> SocAgentChatRequest:
    payload: dict[str, object] = {
        "asset_key": case.query,
        "asset_type": case.asset_type.value,
        "role": case.role,
        "context_refs": {
            "thread_id": thread_id,
            "queue_id": review_item.queue_id,
            "run_id": review_item.run_id,
            "alert_id": review_item.alert_id,
        },
    }
    if case.um:
        payload["um"] = case.um
    return SocAgentChatRequest(
        message="Run the approved D12-B read-only asset location acceptance.",
        thread_id=thread_id,
        queue_id=review_item.queue_id,
        run_id=review_item.run_id,
        allowed_routes=[_ACTION],
        metadata={
            "soc_route": _ACTION,
            "action_payload": payload,
        },
    )


def _default_request_context(
    *,
    case_id: str,
    queue_id: str,
) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id=_DEFAULT_ACTOR_ID,
            actor_type=ActorType.SYSTEM,
            surface=EntrySurface.CLI,
            roles=["soc_admin"],
        ),
        trace_id=f"TRACE-D12B-{uuid4().hex[:12].upper()}",
        idempotency_key=f"d12b-evidence:{queue_id}:{case_id}",
    )


def _blocked_report(
    base: Mapping[str, object],
    *,
    check_id: str,
    detail_code: str,
    error_type: str | None = None,
) -> PingAnD12BEvidenceAcceptanceReport:
    return PingAnD12BEvidenceAcceptanceReport(
        **base,
        status=PingAnD12BEvidenceAcceptanceStatus.BLOCKED,
        checks=[
            PingAnD12BEvidenceCheck(
                check_id=check_id,
                status=PingAnD12BEvidenceCheckStatus.BLOCKED,
                detail_code=detail_code,
            )
        ],
        error_type=error_type,
    )


def _blocked_after_dispatch(
    base: dict[str, object],
    *,
    repository: PingAnD12BEvidenceRepositoryPort,
    run_id: str,
    queue_id: str,
    check_id: str,
    detail_code: str,
    error_type: str | None,
) -> PingAnD12BEvidenceAcceptanceReport:
    _capture_after_state(base, repository=repository, run_id=run_id, queue_id=queue_id)
    return _blocked_report(
        base,
        check_id=check_id,
        detail_code=detail_code,
        error_type=error_type,
    )


def _failed_after_dispatch(
    base: dict[str, object],
    *,
    repository: PingAnD12BEvidenceRepositoryPort,
    run_id: str,
    queue_id: str,
    check_id: str,
    detail_code: str,
    error_type: str,
) -> PingAnD12BEvidenceAcceptanceReport:
    _capture_after_state(base, repository=repository, run_id=run_id, queue_id=queue_id)
    return PingAnD12BEvidenceAcceptanceReport(
        **base,
        status=PingAnD12BEvidenceAcceptanceStatus.FAILED,
        checks=[
            PingAnD12BEvidenceCheck(
                check_id=check_id,
                status=PingAnD12BEvidenceCheckStatus.FAILED,
                detail_code=detail_code,
            )
        ],
        error_type=error_type,
    )


def _capture_after_state(
    base: dict[str, object],
    *,
    repository: PingAnD12BEvidenceRepositoryPort,
    run_id: str,
    queue_id: str,
) -> None:
    try:
        run = repository.get_run(run_id)
        review = repository.get_review_item(queue_id)
    except Exception:  # noqa: BLE001 - original bounded failure remains authoritative
        return
    run_hash = _model_hash(run) if run is not None else None
    review_hash = _model_hash(review) if review is not None else None
    base["run_state_hash_after"] = run_hash
    base["review_state_hash_after"] = review_hash
    base["run_state_unchanged"] = run_hash == base.get("run_state_hash_before")
    base["review_state_unchanged"] = review_hash == base.get("review_state_hash_before")


def _assertion(
    check_id: str,
    passed: bool,
    passed_detail_code: str,
) -> PingAnD12BEvidenceCheck:
    return PingAnD12BEvidenceCheck(
        check_id=check_id,
        status=(PingAnD12BEvidenceCheckStatus.PASSED if passed else PingAnD12BEvidenceCheckStatus.FAILED),
        detail_code=(passed_detail_code if passed else f"{passed_detail_code}:not_observed"),
    )


def _evidence_contract_valid(
    evidence: InvestigationEvidence | None,
    *,
    queue_item: ReviewQueueItem,
    thread_id: str,
) -> bool:
    return bool(
        evidence is not None
        and evidence.route == _ACTION
        and evidence.action == _ACTION
        and evidence.status == "success"
        and evidence.queue_id == queue_item.queue_id
        and evidence.run_id == queue_item.run_id
        and evidence.alert_id == queue_item.alert_id
        and evidence.thread_id == thread_id
    )


def _evidence_visible(
    evidence: list[InvestigationEvidence],
    *,
    evidence_id: object,
) -> bool:
    return isinstance(evidence_id, str) and any(item.evidence_id == evidence_id for item in evidence)


def _bounded_error_type(payload: Mapping[str, object]) -> str | None:
    value = payload.get("error_type")
    if not isinstance(value, str):
        return None
    return value[:256]


def _model_hash(model: BaseModel) -> str:
    return stable_hash(model.model_dump(mode="json"))


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip()
    return normalized.startswith("<") and normalized.endswith(">")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


__all__ = [
    "PingAnD12BEvidenceAcceptanceReport",
    "PingAnD12BEvidenceAcceptanceStatus",
    "PingAnD12BEvidenceCheck",
    "PingAnD12BEvidenceCheckStatus",
    "PingAnD12BEvidenceRepositoryPort",
    "run_pingan_d12b_evidence_acceptance",
]
