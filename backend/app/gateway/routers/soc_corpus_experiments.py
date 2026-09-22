"""DEV batch controls: shared service authority, bounded reads, durable dispatch."""

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager, contextmanager
from threading import Lock
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request

from app.gateway.routers.soc_transport import create_soc_router
from app.gateway.soc_corpus_control import can_configure_corpus
from app.gateway.soc_dependencies import get_or_create_soc_repository, soc_service_context_from_request
from app.gateway.soc_dev_workbench import resolve_soc_dev_workbench_runtime, strict_env_bool
from soc_agent.application.corpus_experiments import CorpusExperimentApplication, build_corpus_experiment_application
from soc_agent.contracts import ACTIVE_PROCESSING_JOB_STATUSES, ProcessingJobStatus, SocMemoryCandidateReviewStage
from soc_agent.contracts.corpus_experiments import (
    Batch,
    CorpusCandidatePage,
    CorpusConcurrencyCommand,
    CorpusExperiment,
    CorpusMemberPage,
    CorpusPrepareCommand,
    CorpusQuickCommand,
    CorpusRound,
    CorpusRoundBrief,
    CorpusRoundCreateCommand,
    CorpusRoundItemPage,
    CorpusRoundProgress,
    CorpusRoundSelection,
    CorpusRoundStartCommand,
    RoundState,
)
from soc_agent.contracts.memory_drafts import MemoryDraftGenerateBatch
from soc_agent.core.errors import SocServiceAuthorizationError, SocServiceConflictError
from soc_agent.core.memory_draft_jobs import DRAFT_WORKLOAD
from soc_agent.core.memory_working_drafts import SocMemoryWorkingDraftService
from soc_agent.db.corpus_experiments import CorpusExperimentConflict, CorpusExperimentSchemaNotReady
from soc_agent.db.jobs import ProcessingJobConflictError
from soc_agent.demo.corpus_experiment_timing import item_timing, round_timing
from soc_agent.demo.corpus_round_comparison import compare_round_item

logger = logging.getLogger(__name__)
_INIT_LOCK = Lock()


def _admin(request: Request):
    context = soc_service_context_from_request(request, include_soc_roles=True)
    if "soc_admin" not in context.actor.roles:
        raise HTTPException(status_code=403, detail="批次验证仅限 DEV 管理员使用")
    return context


def get_corpus_experiment_application(request: Request) -> CorpusExperimentApplication:
    with _INIT_LOCK:
        existing = getattr(request.app.state, "soc_corpus_experiment_application", None)
        if existing is not None:
            return existing
        runtime = resolve_soc_dev_workbench_runtime(request, enabled_flag="SOC_DEV_CORPUS_WORKBENCH_ENABLED")
        from app.gateway.routers.soc_corpus_workbench import get_soc_corpus_workbench_service

        try:
            application = build_corpus_experiment_application(repository=runtime.repository, workbench=get_soc_corpus_workbench_service(request), settings=runtime.settings)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        request.app.state.soc_corpus_experiment_application = application
        return application


@asynccontextmanager
async def experiment_lifespan(app):
    def resume_started_rounds():
        if not strict_env_bool("SOC_DEV_CORPUS_WORKBENCH_ENABLED", False):
            return
        request = Request({"type": "http", "app": app, "headers": []})
        repository = get_or_create_soc_repository(request)
        store = repository.corpus_experiments()
        store.require_schema()
        if store.list_rounds(state="running", limit=1) or store.has_manual_work() or repository.processing_jobs().list_workload_jobs(DRAFT_WORKLOAD, statuses=[ProcessingJobStatus.QUEUED, *ACTIVE_PROCESSING_JOB_STATUSES], limit=1):
            get_corpus_experiment_application(request).dispatcher.start()

    try:
        await asyncio.to_thread(resume_started_rounds)
    except CorpusExperimentSchemaNotReady as exc:
        logger.warning("%s", exc)
    except Exception:
        # Other SOC pages remain available when batch prerequisites need repair.
        logger.exception("Could not resume DEV corpus rounds; check schema and frozen configuration")
    try:
        yield
    finally:
        application = getattr(app.state, "soc_corpus_experiment_application", None)
        if application is not None:
            await asyncio.to_thread(application.dispatcher.stop)


router = create_soc_router(prefix="", tags=["soc-dev-corpus-experiments"])
router.dependencies.append(Depends(_admin))
router.lifespan_context = experiment_lifespan
ConfigurationDep = Annotated[CorpusExperimentApplication, Depends(get_corpus_experiment_application)]


def _ready_application(application: ConfigurationDep) -> CorpusExperimentApplication:
    try:
        application.service.store.require_schema()
    except CorpusExperimentSchemaNotReady as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"X-SOC-Error-Code": "corpus_schema_upgrade_required"}) from exc
    return application


ApplicationDep = Annotated[CorpusExperimentApplication, Depends(_ready_application)]


@contextmanager
def _command_errors():
    try:
        yield
    except (CorpusExperimentConflict, SocServiceConflictError, ProcessingJobConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (PermissionError, SocServiceAuthorizationError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _round(application, round_id):
    result = application.service.store.get_round(round_id)
    if result is None:
        raise HTTPException(status_code=404, detail="该验证轮次不存在")
    return result


def _saved_run_options(application, batch: Batch):
    store = application.service.store
    try:
        store.require_schema()
    except CorpusExperimentSchemaNotReady:
        return application.defaults
    if application.workbench is None:
        return application.defaults
    experiment = store.find_plan_experiment(application.workbench.batch_plan_id)
    if experiment is None:
        return application.defaults
    return store.latest_run_options(experiment.experiment_id, batch) or application.defaults


@router.get("/experiments/configuration")
def configuration(application: ConfigurationDep, request: Request, batch: Batch = "learning"):
    return {
        "defaults": application.defaults.model_dump(mode="json"),
        "full_flow_defaults": application.full_flow_defaults.model_dump(mode="json"),
        "max_concurrency": application.service.capacity.max_concurrency,
        "concurrency_limit": application.service.capacity.ceiling,
        "dispatcher_running": application.dispatcher.is_running,
        "can_configure": can_configure_corpus(request),
        "saved_options": _saved_run_options(application, batch).model_dump(mode="json"),
    }


@router.post("/experiments/concurrency")
def update_concurrency(body: CorpusConcurrencyCommand, request: Request, application: ConfigurationDep):
    context = _admin(request)
    if not can_configure_corpus(request):
        raise HTTPException(status_code=403, detail="最大并发仅限部署本机修改；同事沿用已保存设置。")
    with _command_errors():
        try:
            application.service.capacity.set_limit(body.max_concurrency, actor_id=context.actor.actor_id)
        except OSError as exc:
            logger.warning("Could not persist DEV corpus capacity: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="并发设置保存失败，原设置保持不变，请重试。") from exc
    return {"max_concurrency": application.service.capacity.max_concurrency, "concurrency_limit": application.service.capacity.ceiling}


@router.get("/experiments", response_model=list[CorpusExperiment])
def experiments(application: ApplicationDep, limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    return application.service.store.list_experiments(limit=limit, offset=offset)


@router.post("/experiments", response_model=CorpusExperiment, status_code=201)
def prepare_experiment(body: CorpusPrepareCommand, request: Request, application: ApplicationDep):
    with _command_errors():
        with application.workbench.experiment_preparation_guard():
            return application.service.prepare(application.workbench.batch_plan, experiment_id=body.experiment_id, name=body.name, context=_admin(request))


@router.get("/experiments/{experiment_id}/members", response_model=CorpusMemberPage)
def members(
    experiment_id: str,
    application: ApplicationDep,
    batch: str = "learning",
    scope: str = "reuse",
    group_id: str | None = None,
    rule_code: str | None = None,
    alert_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if application.service.store.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail="该实验不存在")
    with _command_errors():
        selection = CorpusRoundSelection(batch=batch, scope=scope, group_ids=[group_id] if group_id else [], rule_codes=[rule_code] if rule_code else [], alert_ids=[alert_id] if alert_id else [])
        return application.service.store.list_members(experiment_id, selection=selection, limit=limit, offset=offset)


@router.post("/rounds", response_model=CorpusRound, status_code=201)
def create_round(body: CorpusRoundCreateCommand, request: Request, application: ApplicationDep):
    context = _admin(request)
    if not context.idempotency_key:
        raise HTTPException(status_code=400, detail="创建轮次需要 Idempotency-Key，重复提交不会创建第二批任务")
    with _command_errors():
        return application.service.create_round(
            body,
            context=context,
            allow_options_override=can_configure_corpus(request),
            default_options=application.defaults,
            current_plan_id=application.workbench.batch_plan_id if application.workbench is not None else None,
        )


@router.get("/experiments/{experiment_id}/rounds", response_model=list[CorpusRoundBrief])
def round_briefs(experiment_id: str, application: ApplicationDep, batch: Batch | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    return application.service.store.list_round_briefs(experiment_id, batch=batch, limit=limit, offset=offset)


@router.get("/experiments/{experiment_id}/candidates", response_model=CorpusCandidatePage)
def learning_candidates(
    experiment_id: str, application: ApplicationDep, review_stage: SocMemoryCandidateReviewStage = SocMemoryCandidateReviewStage.PENDING, limit: Annotated[int, Query(ge=1, le=100)] = 20, offset: Annotated[int, Query(ge=0)] = 0
):
    if application.service.store.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail="该实验不存在")
    return application.service.store.list_learning_candidates(experiment_id, review_stage=review_stage, limit=limit, offset=offset)


@router.post("/experiments/{experiment_id}/selection", response_model=CorpusMemberPage)
def inspect_selection(experiment_id: str, body: CorpusRoundSelection, application: ApplicationDep, limit: Annotated[int, Query(ge=1, le=100)] = 20, offset: Annotated[int, Query(ge=0)] = 0):
    if application.service.store.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail="该实验不存在")
    return application.service.store.list_members(experiment_id, selection=body, limit=limit, offset=offset)


@router.get("/experiments/{experiment_id}/draft-inputs")
def draft_inputs(experiment_id: str, request: Request, application: ApplicationDep, limit: Annotated[int, Query(ge=1, le=50)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    if application.service.store.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail="该实验不存在")
    page = application.service.store.list_learning_candidates(experiment_id, limit=limit, offset=offset)
    repository = application.service.repository
    drafts = SocMemoryWorkingDraftService(repository=repository, mutation_uow=repository)
    with _command_errors():
        return {"total": page.total, "items": [drafts.get(c.candidate_id, context=_admin(request)).model_dump(mode="json") for c in page.items]}


def _draft_job_projection(job):
    generation = (job.result_payload or {}).get("generated_draft") or {}
    provenance = generation.get("provenance") or {}
    return {
        "job_id": job.job_id,
        "candidate_id": job.input_payload.get("candidate_id"),
        "candidate_revision": job.input_payload.get("candidate_revision"),
        "operator_verdict": job.metadata.get("operator_verdict"),
        "status": job.status.value,
        "version": job.version,
        "attempt_count": job.attempt_count,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "model_name": provenance.get("model_name"),
        "prompt_version": provenance.get("prompt_version"),
        "prompt_hash": provenance.get("prompt_hash"),
        "response_hash": provenance.get("response_hash"),
        "provider_call_count": provenance.get("provider_call_count"),
        "usage": provenance.get("usage"),
        "draft_version": (job.result_payload or {}).get("draft_version"),
        "authority": "draft_only",
    }


@router.post("/experiments/{experiment_id}/draft-jobs", status_code=202)
def submit_draft_jobs(experiment_id: str, body: MemoryDraftGenerateBatch, request: Request, application: ApplicationDep):
    if application.draft_jobs is None:
        raise HTTPException(status_code=503, detail="批量起草尚未配置")
    ids = [c.candidate_id for c in body.commands]
    if application.service.store.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail="该实验不存在")
    if application.service.store.learning_candidate_ids(experiment_id, ids) != set(ids):
        raise HTTPException(status_code=409, detail="只能起草本实验第一批产生的候选")
    with _command_errors():
        jobs = application.draft_jobs.submit_many(body.commands, context=_admin(request), external_ref=experiment_id)
    application.dispatcher.start()
    return {"items": [_draft_job_projection(job) for job in jobs]}


@router.get("/experiments/{experiment_id}/draft-jobs")
def draft_jobs(experiment_id: str, application: ApplicationDep, limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    jobs = application.service.jobs.list_workload_jobs(DRAFT_WORKLOAD, external_ref=experiment_id, limit=limit, offset=offset)
    return {"items": [_draft_job_projection(job) for job in jobs]}


@router.get("/experiments/{experiment_id}/draft-jobs/{job_id}")
def draft_job_result(experiment_id: str, job_id: str, application: ApplicationDep):
    job = application.service.jobs.get(job_id)
    if job is None or job.workload_kind != DRAFT_WORKLOAD or job.external_ref != experiment_id:
        raise HTTPException(status_code=404, detail="该起草任务不存在")
    return {**_draft_job_projection(job), "generated_draft": (job.result_payload or {}).get("generated_draft")}


@router.post("/experiments/{experiment_id}/draft-jobs/{job_id}/retry")
def retry_draft_job(experiment_id: str, job_id: str, request: Request, application: ApplicationDep, expected_version: Annotated[int, Query(ge=1)]):
    job = application.service.jobs.get(job_id)
    if job is None or job.workload_kind != DRAFT_WORKLOAD or job.external_ref != experiment_id:
        raise HTTPException(status_code=404, detail="该起草任务不存在")
    with _command_errors():
        result = application.service.jobs.retry_failed(job_id, expected_version=expected_version, actor_id=_admin(request).actor.actor_id)
    application.dispatcher.start()
    return _draft_job_projection(result)


@router.get("/rounds", response_model=list[CorpusRound])
def rounds(application: ApplicationDep, experiment_id: str | None = None, state: RoundState | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    return application.service.store.list_rounds(experiment_id=experiment_id, state=state, limit=limit, offset=offset)


@router.get("/rounds/{round_id}", response_model=CorpusRoundProgress)
def round_progress(round_id: str, application: ApplicationDep):
    _round(application, round_id)
    progress = application.service.store.round_progress(round_id)
    return progress.model_copy(update={"timing": round_timing(progress)})


@router.get("/rounds/{round_id}/items", response_model=CorpusRoundItemPage)
def round_items(round_id: str, application: ApplicationDep, limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    _round(application, round_id)
    return application.service.store.list_round_items(round_id, limit=limit, offset=offset)


@router.get("/rounds/{round_id}/results")
def round_results(round_id: str, application: ApplicationDep, limit: Annotated[int, Query(ge=1, le=100)] = 100, offset: Annotated[int, Query(ge=0)] = 0):
    round_ = _round(application, round_id)
    experiment = application.service.store.get_experiment(round_.experiment_id)
    if application.workbench.batch_plan_id != experiment.plan_id:
        raise HTTPException(status_code=409, detail="当前样本文件已更换，不能用新样本标签解释旧轮次；请恢复该实验的数据包")
    page = application.service.store.list_round_items(round_id, limit=limit, offset=offset)
    selected = round_.selection.model_copy(update={"alert_ids": [item.alert_id for item in page.items]})
    member_page = application.service.store.list_members(round_.experiment_id, selection=selected, limit=100) if page.items else None
    members = {member.alert_id: member for member in member_page.items} if member_page else {}
    rows = []
    for item in page.items:
        member = members[item.alert_id]
        outcome = item.job.result_payload or {}
        summary = outcome.get("summary", {})
        rows.append(
            {
                "alert_id": item.alert_id,
                "group_id": item.group_id,
                "rule_code": member.rule_code,
                "detection_key": member.detection_key,
                "payload_hash": member.payload_hash,
                "event_time": member.event_time,
                "validation_tier": member.validation_tier,
                "membership_reason": member.reason,
                "status": item.job.status.value,
                "run_id": item.job.run_id,
                "job_id": item.job_id,
                "attempt_count": item.job.attempt_count,
                "job_timing": item_timing(
                    round_,
                    sequence_number=item.sequence_number,
                    created_at=item.job.created_at,
                    started_at=item.job.started_at,
                    completed_at=item.job.completed_at,
                ),
                "candidate_id": outcome.get("candidate_id"),
                "observation_id": outcome.get("observation_id"),
                "pattern_reason": outcome.get("pattern_reason"),
                "summary": summary,
                "attempt_measurements": application.service.repository.journaled_run_measurements(
                    alert_id=member.alert_id,
                    input_hash=member.payload_hash,
                    idempotency_key_hash=hashlib.sha256(json.dumps(item.job.idempotency_key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
                ),
                "snapshot_changed_during_run": outcome.get("snapshot_changed_during_run", False),
                "label": application.workbench.experiment_label(member, decision_available=summary.get("effective_verdict") is not None),
                "error_code": item.job.error_code,
                "error_message": item.job.error_message,
            }
        )
    return {"total": page.total, "items": rows, "round_id": round_id, "source_identity": experiment.source_identity}


@router.get("/rounds/{round_id}/comparison")
def round_comparison(round_id: str, application: ApplicationDep, limit: Annotated[int, Query(ge=1, le=100)] = 20, offset: Annotated[int, Query(ge=0)] = 0):
    round_ = _round(application, round_id)
    if not round_.parent_round_id:
        raise HTTPException(status_code=409, detail="该轮次没有关联旧轮次，不能进行前后对照")
    page = application.service.store.list_round_items(round_id, limit=limit, offset=offset)
    baseline = page.items[0].job.metadata.get("comparison_baseline") if page.items else None
    return {
        "round_id": round_id,
        "parent_round_id": round_.parent_round_id,
        "baseline_captured_at": baseline.get("captured_at") if baseline else None,
        "config_changed": baseline.get("config_hash") != round_.config_hash if baseline else None,
        "total": page.total,
        "items": [compare_round_item(item) for item in page.items],
    }


@router.post("/rounds/{round_id}/start", response_model=CorpusRound, status_code=202)
def start(round_id: str, body: CorpusRoundStartCommand, request: Request, application: ApplicationDep):
    _round(application, round_id)
    with _command_errors():
        result = application.service.start(round_id, context=_admin(request), execution_limit=body.execution_limit, concurrency=body.concurrency)
        application.dispatcher.start()
        return result


@router.post("/rounds/{round_id}/pause", response_model=CorpusRound)
def pause(round_id: str, request: Request, application: ApplicationDep):
    _round(application, round_id)
    with _command_errors():
        return application.service.pause(round_id, context=_admin(request))


@router.post("/rounds/{round_id}/retry-failed")
def retry_failed(round_id: str, request: Request, application: ApplicationDep):
    _round(application, round_id)
    with _command_errors():
        result = application.service.retry_failed(round_id, context=_admin(request))
        if result["retried"]:
            application.dispatcher.start()
        return result


@router.get("/quick-validation")
def quick_validation(application: ApplicationDep, batch: Batch = "learning", scope: str = "all", alert_ids: str = "", offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation

    with _command_errors():
        return CorpusQuickValidation(application.service, application.workbench.batch_plan).snapshot(batch, scope, offset=offset, limit=limit, alert_ids=alert_ids.split(",") if alert_ids else None)


@router.post("/quick-validation", status_code=202)
def quick_validation_command(body: CorpusQuickCommand, request: Request, application: ApplicationDep):
    from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation

    with _command_errors():
        with application.workbench.experiment_preparation_guard():
            result = CorpusQuickValidation(application.service, application.workbench.batch_plan).command(body, context=_admin(request), allow_options_override=can_configure_corpus(request), default_options=application.defaults)
        application.dispatcher.start()
        return result


@router.get("/quick-validation/history/{alert_id}")
def quick_validation_history(alert_id: str, application: ApplicationDep, offset: Annotated[int, Query(ge=0)] = 0):
    from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation
    from soc_agent.demo.corpus_round_comparison import job_result_row

    quick = CorpusQuickValidation(application.service, application.workbench.batch_plan)
    return [job_result_row(job) for job in application.service.store.alert_history(quick.experiment(), alert_id, offset=offset)]
