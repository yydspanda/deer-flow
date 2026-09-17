"""SOC memory candidate API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.gateway.routers.soc_transport import create_soc_router
from app.gateway.soc_dependencies import (
    get_or_create_soc_repository,
    get_soc_review_service,
    soc_service_context_from_request,
)
from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    MemoryPatternDataClass,
    SocMemoryApplicabilitySpec,
    SocMemoryBusinessLesson,
    SocMemoryBusinessLessonDraft,
    SocMemoryCandidate,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateReviewResult,
    SocMemoryCandidateReviewStage,
    SocMemoryCandidateStatus,
    SocMemoryCandidateSupersessionCommand,
    SocMemoryCandidateSupersessionResult,
    SocMemoryCandidateType,
    SocMemoryCenterOverview,
    SocMemoryCenterPatternDetail,
    SocMemoryDecisionDirective,
    SocMemoryFutureUseState,
    SocMemoryLineageReport,
    SocMemoryPatternStageFilter,
    SocMemoryQuery,
    SocMemoryRecord,
    SocMemoryRecordMatchTestCommand,
    SocMemoryRecordMatchTestResult,
    SocMemoryRecordStatus,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMemoryRetrievalActivationResult,
    SocMemoryRetrievalResult,
    SocMemoryRevisionCandidateCreateCommand,
    SocMemoryRevisionCandidateCreateResult,
    SocMemoryRevisionIssueType,
    SocMemoryRevisionProposal,
    SocMemoryRevisionProposalStatus,
    SocMemoryRevisionReviewCommand,
    SocMemoryRevisionReviewDecision,
    SocMemoryRevisionReviewResult,
    SocMemoryRunPromotionCommand,
    SocMemoryRunPromotionResult,
    Verdict,
)
from soc_agent.contracts.memory_drafts import MemoryDraftSaveCommand, MemoryWorkingDraft, MemoryWorkingDraftView
from soc_agent.contracts.memory_governance import MemoryGovernancePreview, MemoryScopeBoundaries, MemoryScopeBoundaryReleaseCommand, MemoryScopeRefinementCommand
from soc_agent.contracts.memory_scope import MemoryScopeView, MemorySourceScopeOptions
from soc_agent.core import (
    SocMemoryCenterService,
    SocMemoryEvolutionError,
    SocMemoryEvolutionService,
    SocMemoryLessonDraftService,
    SocMemoryService,
    SocReviewService,
    SocServiceAuthorizationError,
    SocServiceConflictError,
    SocServiceError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)
from soc_agent.core.memory_working_drafts import SocMemoryWorkingDraftService
from soc_agent.llm import build_configured_memory_lesson_drafter
from soc_agent.memory.scope_view import build_memory_scope_view

router = create_soc_router(prefix="/api/soc/memory", tags=["soc-memory"])


class MemoryCandidateListResponse(BaseModel):
    items: list[SocMemoryCandidate]


class MemoryCandidateDetailResponse(SocMemoryCandidate):
    scope_view: MemoryScopeView | None = None


class MemoryRecordDetailResponse(SocMemoryRecord):
    scope_view: MemoryScopeView | None = None


class MemoryLineageResponse(SocMemoryLineageReport):
    scope_view: MemoryScopeView | None = None


class MemoryRecordListResponse(BaseModel):
    items: list[SocMemoryRecord]
    limit: int
    offset: int
    has_more: bool


class MemoryRevisionProposalListResponse(BaseModel):
    items: list[SocMemoryRevisionProposal]


class MemoryRunPromotionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    note: str | None = Field(default=None, max_length=12_000)
    reason: str | None = Field(
        default=None,
        max_length=12_000,
        description="Deprecated compatibility alias for note.",
    )
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("note", "reason")
    @classmethod
    def normalize_optional_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def resolve_legacy_reason(self) -> MemoryRunPromotionRequest:
        if self.note is not None and self.reason is not None and self.note != self.reason:
            raise ValueError("note and deprecated reason must match when both are provided")
        if self.note is None:
            self.note = self.reason
        return self


class MemoryGovernancePreviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    reviewer_verdict: Verdict | None = None
    promoted_facet_keys: list[str] = Field(default_factory=list, max_length=20)
    promoted_facet_values: dict[str, list[str]] = Field(default_factory=dict, max_length=20)
    selected_behavior_components: list[str] | None = Field(default=None, min_length=1, max_length=100)


class MemoryCandidateReviewRequest(BaseModel):
    decision: SocMemoryCandidateReviewDecision
    reason: str = Field(min_length=1)
    record_summary: str | None = None
    record_content: str | None = None
    record_lesson: SocMemoryBusinessLesson | None = None
    record_applicability: SocMemoryApplicabilitySpec | None = None
    decision_directive: SocMemoryDecisionDirective | None = None
    confirmed_verdict: Verdict | None = None
    apply_to_future_matches: bool = False
    clear_review_on_match: bool = False
    activate_retrieval: bool = False
    restore_predecessor: bool = False
    expected_predecessor_version: int | None = Field(default=None, ge=1)
    activation_valid_until: datetime | None = None
    activation_review_after_days: int | None = Field(default=None, ge=1, le=365)
    replaces_memory_id: str | None = Field(default=None, min_length=1, max_length=64)
    expected_replaced_version: int | None = Field(default=None, ge=1)
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_reviewed_lesson_for_decision_authority(
        self,
    ) -> MemoryCandidateReviewRequest:
        if self.restore_predecessor or self.expected_predecessor_version is not None:
            SocMemoryCandidateReviewCommand(candidate_id="validation", **self.model_dump())
        if (self.replaces_memory_id is None) != (self.expected_replaced_version is None):
            raise ValueError("replacement requires both memory ID and expected version")
        if self.replaces_memory_id is not None and (self.decision is not SocMemoryCandidateReviewDecision.CONFIRM or self.confirmed_verdict is None or self.record_lesson is None):
            raise ValueError("replacement requires confirmation with a reviewed verdict and lesson")
        if (self.apply_to_future_matches or self.decision_directive is not None) and self.record_lesson is None:
            raise ValueError("decision-bearing Memory requires an explicit reviewed record_lesson")
        return self


class MemoryBusinessLessonDraftRequest(BaseModel):
    reviewer_verdict: Verdict
    reviewer_context: str | None = Field(default=None, max_length=4000)
    promoted_facet_keys: list[str] = Field(default_factory=list, max_length=20)
    promoted_facet_values: dict[str, list[str]] = Field(default_factory=dict, max_length=20)
    selected_behavior_components: list[str] | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("promoted_facet_keys")
    @classmethod
    def normalize_promoted_facet_keys(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


class MemoryRetrievalActivationRequest(BaseModel):
    action: SocMemoryRetrievalActivationAction
    expected_record_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    activation_valid_until: datetime | None = None
    review_after_days: int | None = Field(default=None, ge=1, le=365)
    metadata: dict[str, object] = Field(default_factory=dict)


class MemoryRevisionReviewRequest(BaseModel):
    decision: SocMemoryRevisionReviewDecision
    reason: str = Field(min_length=1, max_length=4000)


class MemoryRevisionCandidateCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    expected_record_version: int = Field(ge=1)
    source_run_id: str | None = Field(default=None, min_length=1, max_length=64)
    issue_type: SocMemoryRevisionIssueType
    reason: str = Field(min_length=10, max_length=4000)


class MemoryRecordMatchTestRequest(BaseModel):
    model_config = {"extra": "forbid"}

    run_id: str | None = Field(default=None, min_length=1, max_length=64)
    alert_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_one_run_locator(self) -> MemoryRecordMatchTestRequest:
        if (self.run_id is None) == (self.alert_id is None):
            raise ValueError("provide exactly one of run_id or alert_id")
        return self


class MemoryCandidateSupersessionRequest(BaseModel):
    successor_candidate_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


def get_soc_memory_service(request: Request) -> SocMemoryService:
    injected = getattr(request.app.state, "soc_memory_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    return SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        memory_evolution_repository=repository,
        mutation_audit_repository=repository,
        mutation_uow=repository,
        analysis_run_repository=repository,
        profile_registry=build_soc_memory_profile_registry(),
    )


MemoryServiceDep = Annotated[SocMemoryService, Depends(get_soc_memory_service)]
ReviewServiceDep = Annotated[SocReviewService, Depends(get_soc_review_service)]


def get_soc_memory_center_service(request: Request) -> SocMemoryCenterService:
    injected = getattr(request.app.state, "soc_memory_center_service", None)
    if injected is not None:
        return injected
    repository = get_or_create_soc_repository(request)
    return SocMemoryCenterService(
        center_repository=repository,
        observation_repository=repository,
        candidate_repository=repository,
        record_repository=repository,
        profile_registry=build_soc_memory_profile_registry(),
    )


MemoryCenterServiceDep = Annotated[
    SocMemoryCenterService,
    Depends(get_soc_memory_center_service),
]


def get_soc_memory_lesson_draft_service(
    request: Request,
) -> SocMemoryLessonDraftService:
    injected = getattr(request.app.state, "soc_memory_lesson_draft_service", None)
    if injected is not None:
        return injected
    repository = get_or_create_soc_repository(request)
    service = SocMemoryLessonDraftService(
        candidate_repository=repository,
        drafter=build_configured_memory_lesson_drafter(),
        governance_service=get_soc_memory_service(request),
        profile_registry=build_soc_memory_profile_registry(),
    )
    request.app.state.soc_memory_lesson_draft_service = service
    return service


MemoryLessonDraftServiceDep = Annotated[
    SocMemoryLessonDraftService,
    Depends(get_soc_memory_lesson_draft_service),
]


def get_soc_memory_working_draft_service(request: Request) -> SocMemoryWorkingDraftService:
    repository = get_or_create_soc_repository(request)
    return SocMemoryWorkingDraftService(repository=repository, mutation_uow=repository)


WorkingDraftServiceDep = Annotated[SocMemoryWorkingDraftService, Depends(get_soc_memory_working_draft_service)]


@router.get("/candidates/{candidate_id}/working-draft", response_model=MemoryWorkingDraftView)
def get_memory_working_draft(candidate_id: str, request: Request, service: WorkingDraftServiceDep):
    try:
        return service.get(candidate_id, context=soc_service_context_from_request(request, include_soc_roles=True))
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/candidates/{candidate_id}/working-draft", response_model=MemoryWorkingDraft)
def save_memory_working_draft(candidate_id: str, payload: MemoryDraftSaveCommand, request: Request, service: WorkingDraftServiceDep):
    try:
        return service.save(candidate_id, payload, context=soc_service_context_from_request(request, include_soc_roles=True))
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def get_soc_memory_evolution_service(request: Request) -> SocMemoryEvolutionService:
    injected = getattr(request.app.state, "soc_memory_evolution_service", None)
    if injected is not None:
        return injected
    repository = get_or_create_soc_repository(request)
    return SocMemoryEvolutionService(
        repository=repository,
        memory_record_repository=repository,
        automation_repository=repository,
        mutation_audit_repository=repository,
        mutation_uow=repository,
    )


MemoryEvolutionServiceDep = Annotated[
    SocMemoryEvolutionService,
    Depends(get_soc_memory_evolution_service),
]


@router.get("/candidates", response_model=MemoryCandidateListResponse)
def list_memory_candidates(
    service: MemoryServiceDep,
    status: Annotated[SocMemoryCandidateStatus | None, Query()] = None,
    review_stage: Annotated[SocMemoryCandidateReviewStage | None, Query()] = None,
    tenant_scope: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    alert_id: str | None = Query(default=None),
    queue_id: str | None = Query(default=None),
    revision_of_memory_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryCandidateListResponse:
    if review_stage is not None and status is not None:
        raise HTTPException(status_code=422, detail="Use either review_stage or status, not both")
    if review_stage is None and status is None:
        status = SocMemoryCandidateStatus.PENDING_REVIEW
    try:
        return MemoryCandidateListResponse(
            items=service.list_candidates(
                status=status,
                review_stage=review_stage,
                tenant_scope=tenant_scope,
                tenant_id=tenant_id,
                run_id=run_id,
                alert_id=alert_id,
                queue_id=queue_id,
                revision_of_memory_id=revision_of_memory_id,
                limit=limit,
            )
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/center", response_model=SocMemoryCenterOverview)
def get_memory_center_overview(
    service: MemoryCenterServiceDep,
    tenant_id: str | None = Query(default=None),
    environment: str | None = Query(default=None),
    data_class: MemoryPatternDataClass | None = Query(default=None),
    profile_id: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=256),
    include_terminal_history: bool = Query(default=False),
    stage: SocMemoryPatternStageFilter | None = Query(default=None),
    future_use: SocMemoryFutureUseState | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SocMemoryCenterOverview:
    try:
        return service.overview(
            tenant_id=tenant_id,
            environment=environment,
            data_class=data_class,
            profile_id=profile_id,
            search=search,
            include_terminal_history=include_terminal_history,
            stage=stage,
            future_use=future_use,
            limit=limit,
            offset=offset,
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/center/patterns/{lineage_key}",
    response_model=SocMemoryCenterPatternDetail,
)
def get_memory_center_pattern(
    lineage_key: str,
    service: MemoryCenterServiceDep,
    include_observations: bool = Query(default=True),
    observation_limit: int = Query(default=100, ge=1, le=500),
    observation_offset: int = Query(default=0, ge=0),
) -> SocMemoryCenterPatternDetail:
    try:
        return service.pattern_detail(
            lineage_key,
            include_observations=include_observations,
            observation_limit=observation_limit,
            observation_offset=observation_offset,
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/candidates/{candidate_id}", response_model=MemoryCandidateDetailResponse)
def get_memory_candidate(candidate_id: str, service: MemoryServiceDep) -> MemoryCandidateDetailResponse:
    try:
        candidate = service.get_candidate(candidate_id)
        return MemoryCandidateDetailResponse(**candidate.model_dump(), scope_view=build_memory_scope_view(candidate.applicability, candidate.facets, registry=build_soc_memory_profile_registry()))
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/runs/{run_id}/promote",
    response_model=SocMemoryRunPromotionResult,
    response_model_exclude_none=True,
)
def promote_run_to_memory_candidate(
    run_id: str,
    body: MemoryRunPromotionRequest,
    request: Request,
    service: ReviewServiceDep,
) -> SocMemoryRunPromotionResult:
    """Explicitly promote one completed run into the governed review inbox."""

    context = soc_service_context_from_request(request, include_soc_roles=True)
    if context.idempotency_key is None:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    try:
        return service.promote_run_to_memory(
            SocMemoryRunPromotionCommand(
                run_id=run_id,
                note=body.note,
                confidence=body.confidence,
                metadata={"source": "soc_web_run_promotion"},
            ),
            context=context,
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/records/{memory_id}/scope-boundaries", response_model=MemoryScopeBoundaries)
def get_memory_scope_boundaries(memory_id: str, service: MemoryServiceDep) -> MemoryScopeBoundaries:
    try:
        return service.scope_boundaries(memory_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/records/{memory_id}/scope-boundaries/release", response_model=SocMemoryRecord)
def release_memory_scope_boundary(memory_id: str, payload: MemoryScopeBoundaryReleaseCommand, request: Request, service: MemoryServiceDep) -> SocMemoryRecord:
    if memory_id != payload.memory_id:
        raise HTTPException(status_code=422, detail="经验编号不一致")
    context = soc_service_context_from_request(request, include_soc_roles=True)
    if context.idempotency_key is None:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    try:
        return service.release_scope_boundary(payload, context=context)
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/candidates/{candidate_id}/scope-options", response_model=MemorySourceScopeOptions)
def get_memory_candidate_scope_options(
    candidate_id: str,
    service: MemoryServiceDep,
    facet_key: str | None = Query(default=None, pattern="^(entity|role_entity)$"),
    prefix: str | None = Query(default=None, max_length=40),
    search: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    try:
        return service.candidate_scope_options(candidate_id, facet_key=facet_key, prefix=prefix, search=search, offset=offset, limit=limit)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/governance-preview", response_model=MemoryGovernancePreview)
def preview_memory_candidate_governance(candidate_id: str, payload: MemoryGovernancePreviewRequest, service: MemoryServiceDep) -> MemoryGovernancePreview:
    try:
        return service.preview_candidate_governance(
            candidate_id, reviewer_verdict=payload.reviewer_verdict, promoted_facet_keys=payload.promoted_facet_keys, promoted_facet_values=payload.promoted_facet_values, selected_behavior_components=payload.selected_behavior_components
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/refinements", response_model=SocMemoryCandidate)
def refine_memory_candidate_scope(candidate_id: str, payload: MemoryScopeRefinementCommand, request: Request, service: MemoryServiceDep) -> SocMemoryCandidate:
    if candidate_id != payload.candidate_id:
        raise HTTPException(status_code=422, detail="候选编号不一致")
    context = soc_service_context_from_request(request, include_soc_roles=True)
    if context.idempotency_key is None:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    try:
        return service.refine_candidate_scope(payload, context=context)
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/review", response_model=SocMemoryCandidateReviewResult)
def review_memory_candidate(
    candidate_id: str,
    payload: MemoryCandidateReviewRequest,
    request: Request,
    service: MemoryServiceDep,
) -> SocMemoryCandidateReviewResult:
    try:
        return service.review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=candidate_id,
                decision=payload.decision,
                reason=payload.reason,
                record_summary=payload.record_summary,
                record_content=payload.record_content,
                record_lesson=payload.record_lesson,
                record_applicability=payload.record_applicability,
                decision_directive=payload.decision_directive,
                confirmed_verdict=payload.confirmed_verdict,
                apply_to_future_matches=payload.apply_to_future_matches,
                clear_review_on_match=payload.clear_review_on_match,
                activate_retrieval=payload.activate_retrieval,
                restore_predecessor=payload.restore_predecessor,
                expected_predecessor_version=payload.expected_predecessor_version,
                activation_valid_until=payload.activation_valid_until,
                activation_review_after_days=payload.activation_review_after_days,
                replaces_memory_id=payload.replaces_memory_id,
                expected_replaced_version=payload.expected_replaced_version,
                metadata=payload.metadata,
            ),
            context=soc_service_context_from_request(request, include_soc_roles=True),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/candidates/{candidate_id}/supersession",
    response_model=SocMemoryCandidateSupersessionResult,
)
def supersede_memory_candidate(
    candidate_id: str,
    payload: MemoryCandidateSupersessionRequest,
    request: Request,
    service: MemoryServiceDep,
) -> SocMemoryCandidateSupersessionResult:
    try:
        return service.supersede_candidate(
            SocMemoryCandidateSupersessionCommand(
                candidate_id=candidate_id,
                successor_candidate_id=payload.successor_candidate_id,
                reason=payload.reason,
            ),
            context=soc_service_context_from_request(
                request,
                include_soc_roles=True,
            ),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/candidates/{candidate_id}/lesson-draft",
    response_model=SocMemoryBusinessLessonDraft,
)
def draft_memory_business_lesson(
    candidate_id: str,
    payload: MemoryBusinessLessonDraftRequest,
    request: Request,
    service: MemoryLessonDraftServiceDep,
) -> SocMemoryBusinessLessonDraft:
    try:
        return service.draft_business_lesson(
            candidate_id,
            reviewer_verdict=payload.reviewer_verdict,
            reviewer_context=payload.reviewer_context,
            promoted_facet_keys=payload.promoted_facet_keys,
            promoted_facet_values=payload.promoted_facet_values,
            selected_behavior_components=payload.selected_behavior_components,
            context=soc_service_context_from_request(
                request,
                include_soc_roles=True,
            ),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/records", response_model=MemoryRecordListResponse)
def list_memory_records(
    service: MemoryServiceDep,
    status: SocMemoryRecordStatus | None = Query(default=SocMemoryRecordStatus.CONFIRMED),
    memory_type: SocMemoryCandidateType | None = Query(default=None),
    tenant_scope: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    source_candidate_id: str | None = Query(default=None),
    source_run_id: str | None = Query(default=None),
    source_alert_id: str | None = Query(default=None),
    retrieval_enabled: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> MemoryRecordListResponse:
    try:
        records = service.list_records(
            status=status,
            memory_type=memory_type,
            tenant_scope=tenant_scope,
            tenant_id=tenant_id,
            source_candidate_id=source_candidate_id,
            source_run_id=source_run_id,
            source_alert_id=source_alert_id,
            retrieval_enabled=retrieval_enabled,
            search=search,
            limit=limit + 1,
            offset=offset,
        )
        return MemoryRecordListResponse(
            items=records[:limit],
            limit=limit,
            offset=offset,
            has_more=len(records) > limit,
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/records/{memory_id}", response_model=MemoryRecordDetailResponse)
def get_memory_record(memory_id: str, service: MemoryServiceDep) -> MemoryRecordDetailResponse:
    try:
        record = service.get_record(memory_id)
        return MemoryRecordDetailResponse(**record.model_dump(), scope_view=build_memory_scope_view(record.applicability, record.facets, registry=build_soc_memory_profile_registry()))
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/records/{memory_id}/match-test",
    response_model=SocMemoryRecordMatchTestResult,
)
def test_memory_record_match(
    memory_id: str,
    payload: MemoryRecordMatchTestRequest,
    service: MemoryServiceDep,
) -> SocMemoryRecordMatchTestResult:
    try:
        return service.test_record_match(
            SocMemoryRecordMatchTestCommand(
                memory_id=memory_id,
                run_id=payload.run_id,
                alert_id=payload.alert_id,
            )
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/records/{memory_id}/revision-candidates",
    response_model=SocMemoryRevisionCandidateCreateResult,
)
def create_memory_revision_candidate(
    memory_id: str,
    payload: MemoryRevisionCandidateCreateRequest,
    request: Request,
    service: MemoryServiceDep,
) -> SocMemoryRevisionCandidateCreateResult:
    context = soc_service_context_from_request(request, include_soc_roles=True)
    if context.idempotency_key is None:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    try:
        return service.propose_revision_candidate(
            SocMemoryRevisionCandidateCreateCommand(
                memory_id=memory_id,
                expected_record_version=payload.expected_record_version,
                source_run_id=payload.source_run_id,
                issue_type=payload.issue_type,
                reason=payload.reason,
            ),
            context=context,
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/records/{memory_id}/lineage",
    response_model=MemoryLineageResponse,
)
def get_memory_lineage(
    memory_id: str,
    service: MemoryEvolutionServiceDep,
) -> MemoryLineageResponse:
    try:
        lineage = service.get_lineage(memory_id)
        return MemoryLineageResponse(**lineage.model_dump(), scope_view=build_memory_scope_view(lineage.record.applicability, lineage.record.facets, registry=build_soc_memory_profile_registry()))
    except SocMemoryEvolutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/revisions",
    response_model=MemoryRevisionProposalListResponse,
)
def list_memory_revision_proposals(
    service: MemoryEvolutionServiceDep,
    memory_id: str | None = Query(default=None),
    status: SocMemoryRevisionProposalStatus | None = Query(default=SocMemoryRevisionProposalStatus.PENDING_REVIEW),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryRevisionProposalListResponse:
    return MemoryRevisionProposalListResponse(
        items=service.list_revision_proposals(
            memory_id=memory_id,
            status=status,
            limit=limit,
        )
    )


@router.get(
    "/revisions/{proposal_id}",
    response_model=SocMemoryRevisionProposal,
)
def get_memory_revision_proposal(
    proposal_id: str,
    service: MemoryEvolutionServiceDep,
) -> SocMemoryRevisionProposal:
    try:
        return service.get_revision_proposal(proposal_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/revisions/{proposal_id}/review",
    response_model=SocMemoryRevisionReviewResult,
)
def review_memory_revision_proposal(
    proposal_id: str,
    payload: MemoryRevisionReviewRequest,
    request: Request,
    service: MemoryEvolutionServiceDep,
) -> SocMemoryRevisionReviewResult:
    try:
        return service.review_revision_proposal(
            SocMemoryRevisionReviewCommand(
                proposal_id=proposal_id,
                decision=payload.decision,
                reason=payload.reason,
            ),
            context=soc_service_context_from_request(
                request,
                include_soc_roles=True,
            ),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SocMemoryEvolutionError, SocServiceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/records/{memory_id}/retrieval",
    response_model=SocMemoryRetrievalActivationResult,
)
def update_memory_retrieval_activation(
    memory_id: str,
    payload: MemoryRetrievalActivationRequest,
    request: Request,
    service: MemoryServiceDep,
) -> SocMemoryRetrievalActivationResult:
    try:
        return service.set_retrieval_activation(
            SocMemoryRetrievalActivationCommand(
                memory_id=memory_id,
                action=payload.action,
                expected_record_version=payload.expected_record_version,
                reason=payload.reason,
                activation_valid_until=payload.activation_valid_until,
                review_after_days=payload.review_after_days,
                metadata=payload.metadata,
            ),
            context=soc_service_context_from_request(request, include_soc_roles=True),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/search", response_model=SocMemoryRetrievalResult)
def search_memory_records(payload: SocMemoryQuery, service: MemoryServiceDep) -> SocMemoryRetrievalResult:
    try:
        return service.find_relevant_records(payload)
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
