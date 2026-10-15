"""The batch executor calls the normal SOC analysis and Pattern services."""

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta

from soc_agent.contracts import AnalysisRunRecoveryCommand, AnalysisRunStatus, AuditAction, MemoryPatternDataClass, MemoryPatternSourceType, ServiceRequestContext
from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusExperimentMember, CorpusRound, CorpusSemanticRetry
from soc_agent.contracts.memory_patterns import MemoryPatternAccumulationScope
from soc_agent.core import SocAnalysisService, SocMemoryPatternService
from soc_agent.core.case_outcomes import project_soc_case_outcome
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.demo.corpus_experiments import CorpusExecutionError
from soc_agent.demo.corpus_rebalance import fact_identity_hash, independent_learning_run, learning_run_hash, project_rebalance_fact
from soc_agent.memory.patterns import MemoryPatternIneligibleError
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.utils.hashing import stable_hash


class CorpusRuntimeExecutor:
    def __init__(self, *, repository: SqlAlchemyAlertRepository, load_payload: Callable[[CorpusExperimentMember], dict], analysis_factory: Callable[[CorpusRound], SocAnalysisService], profile_registry: SocMemoryProfileRegistry):
        self._repository = repository
        self._load_payload = load_payload
        self._analysis_factory = analysis_factory
        self._profiles = profile_registry

    def __call__(self, round_: CorpusRound, member: CorpusExperimentMember, context: ServiceRequestContext, *, semantic_retry: CorpusSemanticRetry | None = None) -> CorpusExecutionOutcome:
        payload = self._load_payload(member)
        if stable_hash(payload) != member.payload_hash:
            raise CorpusExecutionError("corpus payload no longer matches the fixed member", block_round=True)
        experiment = self._repository.corpus_experiments().get_experiment(round_.experiment_id)
        imported_id = experiment.rebalance.learning_run_imports.get(member.alert_id) if experiment and experiment.rebalance and round_.selection.batch == "learning" and round_.purpose != "facts" else None
        imported = self._repository.get_run(imported_id) if imported_id else None
        if imported_id and (not independent_learning_run(imported) or imported.alert_id != member.alert_id or imported.input_hash != member.payload_hash or self._repository.list_memory_uses(run_id=imported_id, limit=1)):
            raise CorpusExecutionError("inherited learning source is no longer eligible", block_round=True)
        if imported_id and experiment.rebalance.learning_run_hashes.get(member.alert_id) != learning_run_hash(imported):
            raise CorpusExecutionError("inherited learning source changed after the plan was frozen", block_round=True)
        if semantic_retry:
            # An explicit fact refresh must not return the inherited result again.
            imported = None
        execution_round = round_
        if semantic_retry:
            previous = self._repository.get_run(semantic_retry.previous_run_id)
            if round_.purpose == "facts" or round_.options.normalization_review_mode != "apply":
                raise CorpusExecutionError("semantic recovery requires semantic review enabled in an analysis round", block_round=True)
            if previous is None or previous.alert_id != member.alert_id or previous.input_hash != member.payload_hash or previous.input_payload != payload:
                raise CorpusExecutionError("semantic recovery source does not match the fixed member", block_round=True)
            # This explicit, journaled recovery bypasses the fact cache once per
            # attempt. The persisted round and its behavior snapshot stay intact.
            execution_round = round_.model_copy(update={"options": round_.options.model_copy(update={"refresh_normalization": True})})
        service = self._analysis_factory(execution_round) if imported is None else None
        prior = None
        if context.idempotency_key:
            # Preserve the existing request-journal hash encoding, not the corpus hash.
            key_hash = hashlib.sha256(json.dumps(context.idempotency_key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
            prior = self._repository.find_journaled_run(alert_id=member.alert_id, input_hash=member.payload_hash, idempotency_key_hash=key_hash)
        if imported is not None:
            run = imported
        elif prior is not None and prior.status in {AnalysisRunStatus.RUNNING, AnalysisRunStatus.INTERRUPTED}:
            # Only the lease-fenced dispatcher invokes this executor. It owns the
            # alert after the prior worker's lease expired, not merely after a timeout.
            run = service.recover(AnalysisRunRecoveryCommand(run_id=prior.run_id, reason="Corpus worker reacquired the durable alert lease", stale_after_seconds=0), context=context)
        elif prior is not None and prior.request_journal.action == AuditAction.REPLAY and prior.replay_of_run_id:
            run = service.replay(prior.replay_of_run_id, context=context)
        elif semantic_retry:
            run = service.replay(semantic_retry.previous_run_id, context=context)
        else:
            run = service.analyze(payload, context=context)
        # Runtime only assigns the canonical identity after normalization. A
        # failure before that point belongs to this member only when both the
        # saved input and its hash still match the frozen corpus payload.
        failed_before_identity = (
            run.status == AnalysisRunStatus.FAILED
            and run.alert_id == "unknown"
            and run.normalized_alert is None
            and run.failure is not None
            and run.failure.step_name == "normalize"
            and run.input_hash == member.payload_hash
            and stable_hash(run.input_payload) == member.payload_hash
        )
        if run.alert_id != member.alert_id and not failed_before_identity:
            raise CorpusExecutionError("Runtime returned a different alert identity", run_id=run.run_id, block_round=True)
        if run.status == AnalysisRunStatus.FAILED:
            failure = run.failure
            raise CorpusExecutionError(
                failure.message if failure else "Runtime failed", run_id=run.run_id, retryable=bool(failure and failure.retryable), block_round=bool(failure and failure.error_type in {"AuthenticationError", "PermissionDeniedError"})
            )
        if run.status == AnalysisRunStatus.RUNNING:
            raise CorpusExecutionError("prior Runtime journal is still running; recover the stale Run before retrying", run_id=run.run_id)
        if round_.purpose == "facts":
            if run.status != AnalysisRunStatus.FACTS_READY or run.analysis is not None or run.decision is not None:
                raise CorpusExecutionError("facts preprocessing returned an analysis result", run_id=run.run_id, block_round=True)
            fact = project_rebalance_fact(run, self._profiles, environment=experiment.environment)
            return CorpusExecutionOutcome(
                run_id=run.run_id,
                pattern_reason="facts_only_no_learning",
                summary={
                    "analysis_status": run.status.value,
                    "base_model_evaluated": False,
                    "processing_path": "facts_only",
                    "rebalance_fact": fact.model_dump(mode="json"),
                    "measurements": self._repository.get_run_measurements(run.run_id),
                    "total_duration_ms": run.total_duration_ms,
                },
            )
        if experiment and experiment.rebalance and (expected := experiment.rebalance.fact_hashes.get(member.alert_id)):
            if fact_identity_hash(project_rebalance_fact(run, self._profiles, environment=experiment.environment)) != expected:
                raise CorpusExecutionError("semantic facts changed after the membership was frozen; prepare a new successor plan", run_id=run.run_id, block_round=True)
        transitions = self._repository.list_decision_transitions(run_id=run.run_id, limit=1)
        transition = transitions[0] if transitions else None
        uses = self._repository.list_memory_uses(run_id=run.run_id, limit=500)
        case = project_soc_case_outcome(run, decision_transition=transition)
        summary = {
            "analysis_status": run.status.value,
            "normalization_assist_status": run.normalization_assistance.status if run.normalization_assistance else None,
            "total_duration_ms": run.total_duration_ms,
            "phase_timings": [{"phase": step.step_name, "status": step.status.value, "duration_ms": step.duration_ms} for step in run.steps],
            "model_name": run.model_name,
            "prompt_version": run.prompt_version,
            "base_verdict": run.decision.verdict.value if run.decision else None,
            "base_model_evaluated": run.direct_resolution is None,
            "effective_verdict": case.security_verdict.value if case.security_verdict else None,
            "recommended_handling": case.recommended_handling,
            "decision_usable": case.decision_usable,
            "processing_path": case.processing_path,
            "memory_uses": [use.model_dump(mode="json") for use in uses],
            "decision_stages": [stage.model_dump(mode="json") for stage in transition.stages] if transition else [],
            "measurements": self._repository.get_run_measurements(run.run_id),
        }
        if semantic_retry:
            summary["semantic_retry"] = semantic_retry.model_dump(mode="json")
        if imported is not None:
            # Historical provider usage is provenance, not expenditure of this job.
            summary.update(inherited_from_run_id=imported.run_id, inherited_from_experiment_id=experiment.rebalance.parent_experiment_id, measurements={}, total_duration_ms=0, phase_timings=[], base_model_evaluated=False)
        accumulated = accumulate_corpus_run(repository=self._repository, profiles=self._profiles, round_=round_, member=member, run=run, context=context)
        summary.update(accumulated.summary)
        return accumulated.model_copy(update={"summary": summary})


def accumulate_corpus_run(*, repository, profiles, round_, member, run, context) -> CorpusExecutionOutcome:
    """Project a saved successful Run into Pattern state, without invoking a model."""
    observation_id = candidate_id = None
    pattern_reason = "validation_learning_disabled"
    summary = {}
    continuous = round_.pattern_accumulation_scope is not None
    if round_.selection.batch == "learning" or continuous:
        if continuous and run.normalization_assistance is not None and run.normalization_assistance.mode == "apply" and run.normalization_assistance.status == "failed":
            pattern_reason = "semantic_review_failed_learning_deferred"
        elif run.direct_resolution is not None and (not continuous or run.direct_resolution.source_kind != "memory"):
            pattern_reason = "direct_resolution_not_an_independent_sample"
        else:
            experiment = repository.corpus_experiments().get_experiment(round_.experiment_id)
            service = SocMemoryPatternService(repository=repository, candidate_repository=repository, profile_registry=profiles, accumulation_scope=corpus_accumulation_scope(repository, round_))
            try:
                result = service.observe_run(
                    run,
                    source_type=MemoryPatternSourceType.BATCH_ALERT,
                    transport_ref=f"corpus-experiment:{round_.experiment_id}:{member.alert_id}:run:{run.run_id}",
                    environment=experiment.environment,
                    data_class=MemoryPatternDataClass.OPERATIONAL,
                    context=context,
                    source_metadata={"corpus_batch": round_.selection.batch, "corpus_round_id": round_.round_id, "configuration_hash": round_.config_hash},
                )
                observation_id = result.observation.observation_id
                candidate_id = result.candidate.candidate_id if result.candidate else None
                pattern_reason = result.note
                summary.update(
                    pattern_support_count=result.support_count,
                    candidate_created=result.candidate_created,
                    pattern_quality_gate=result.cohort_quality.quality_gate_passed,
                    pattern_reason_codes=result.cohort_quality.reason_codes,
                    actual_pattern=result.observation.signature.model_dump(mode="json"),
                )
            except MemoryPatternIneligibleError as exc:
                pattern_reason = str(exc)
            except Exception as exc:
                # Analysis is already saved. A retry reuses its idempotency key.
                raise CorpusExecutionError(f"pattern accumulation failed: {type(exc).__name__}: {exc}", run_id=run.run_id, retryable=True) from exc
    return CorpusExecutionOutcome(run_id=run.run_id, observation_id=observation_id, candidate_id=candidate_id, pattern_reason=pattern_reason, summary=summary)


def corpus_accumulation_scope(repository, round_: CorpusRound) -> MemoryPatternAccumulationScope:
    if round_.pattern_accumulation_scope is not None:
        return round_.pattern_accumulation_scope
    first, last = repository.corpus_experiments().learning_event_range(round_.experiment_id)
    return MemoryPatternAccumulationScope(
        experiment_id=round_.experiment_id,
        configuration_hash=round_.config_hash,
        learning_generation=round_.learning_generation,
        event_start=first,
        event_end=last + timedelta(microseconds=1),
    )
