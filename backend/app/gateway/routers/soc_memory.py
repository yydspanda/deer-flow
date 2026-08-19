"""SOC memory candidate API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.gateway.routers.soc_transport import create_soc_router
from app.gateway.soc_dependencies import (
    get_or_create_soc_repository,
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
    SocMemoryCandidateStatus,
    SocMemoryCandidateSupersessionCommand,
    SocMemoryCandidateSupersessionResult,
    SocMemoryCenterOverview,
    SocMemoryCenterPatternDetail,
    SocMemoryDecisionDirective,
    SocMemoryLineageReport,
    SocMemoryQuery,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMemoryRetrievalActivationResult,
    SocMemoryRetrievalResult,
    SocMemoryRevisionProposal,
    SocMemoryRevisionProposalStatus,
    SocMemoryRevisionReviewCommand,
    SocMemoryRevisionReviewDecision,
    SocMemoryRevisionReviewResult,
    Verdict,
)
from soc_agent.core import (
    SocMemoryCenterService,
    SocMemoryEvolutionError,
    SocMemoryEvolutionService,
    SocMemoryLessonDraftService,
    SocMemoryService,
    SocServiceAuthorizationError,
    SocServiceConflictError,
    SocServiceError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)
from soc_agent.llm import build_configured_memory_lesson_drafter

router = create_soc_router(prefix="/api/soc/memory", tags=["soc-memory"])


class MemoryCandidateListResponse(BaseModel):
    items: list[SocMemoryCandidate]


class MemoryRecordListResponse(BaseModel):
    items: list[SocMemoryRecord]


class MemoryRevisionProposalListResponse(BaseModel):
    items: list[SocMemoryRevisionProposal]


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
    activation_valid_until: datetime | None = None
    activation_review_after_days: int | None = Field(default=None, ge=1, le=365)
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_reviewed_lesson_for_decision_authority(
        self,
    ) -> MemoryCandidateReviewRequest:
        if (self.apply_to_future_matches or self.decision_directive is not None) and self.record_lesson is None:
            raise ValueError("decision-bearing Memory requires an explicit reviewed record_lesson")
        return self


class MemoryBusinessLessonDraftRequest(BaseModel):
    reviewer_verdict: Verdict
    reviewer_context: str | None = Field(default=None, max_length=4000)
    promoted_facet_keys: list[str] = Field(default_factory=list, max_length=20)

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


class MemoryCandidateSupersessionRequest(BaseModel):
    successor_candidate_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


def get_soc_memory_service(request: Request) -> SocMemoryService:
    injected = getattr(request.app.state, "soc_memory_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    return SocMemoryService(candidate_repository=repository, record_repository=repository)


MemoryServiceDep = Annotated[SocMemoryService, Depends(get_soc_memory_service)]


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
    )
    request.app.state.soc_memory_lesson_draft_service = service
    return service


MemoryLessonDraftServiceDep = Annotated[
    SocMemoryLessonDraftService,
    Depends(get_soc_memory_lesson_draft_service),
]


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
    status: SocMemoryCandidateStatus | None = Query(default=SocMemoryCandidateStatus.PENDING_REVIEW),
    tenant_scope: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    alert_id: str | None = Query(default=None),
    queue_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryCandidateListResponse:
    try:
        return MemoryCandidateListResponse(
            items=service.list_candidates(
                status=status,
                tenant_scope=tenant_scope,
                tenant_id=tenant_id,
                run_id=run_id,
                alert_id=alert_id,
                queue_id=queue_id,
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


@router.get("/candidates/{candidate_id}", response_model=SocMemoryCandidate)
def get_memory_candidate(candidate_id: str, service: MemoryServiceDep) -> SocMemoryCandidate:
    try:
        return service.get_candidate(candidate_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
                activation_valid_until=payload.activation_valid_until,
                activation_review_after_days=payload.activation_review_after_days,
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
    tenant_scope: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    source_candidate_id: str | None = Query(default=None),
    retrieval_enabled: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryRecordListResponse:
    try:
        return MemoryRecordListResponse(
            items=service.list_records(
                status=status,
                tenant_scope=tenant_scope,
                tenant_id=tenant_id,
                source_candidate_id=source_candidate_id,
                retrieval_enabled=retrieval_enabled,
                limit=limit,
            )
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/records/{memory_id}", response_model=SocMemoryRecord)
def get_memory_record(memory_id: str, service: MemoryServiceDep) -> SocMemoryRecord:
    try:
        return service.get_record(memory_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/records/{memory_id}/lineage",
    response_model=SocMemoryLineageReport,
)
def get_memory_lineage(
    memory_id: str,
    service: MemoryEvolutionServiceDep,
) -> SocMemoryLineageReport:
    try:
        return service.get_lineage(memory_id)
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
