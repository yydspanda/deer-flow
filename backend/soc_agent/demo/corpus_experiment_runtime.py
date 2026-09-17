"""The batch executor calls the normal SOC analysis and Pattern services."""

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta

from soc_agent.contracts import AnalysisRunRecoveryCommand, AnalysisRunStatus, AuditAction, MemoryPatternDataClass, MemoryPatternSourceType, ServiceRequestContext
from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusExperimentMember, CorpusRound
from soc_agent.contracts.memory_patterns import MemoryPatternAccumulationScope
from soc_agent.core import SocAnalysisService, SocMemoryPatternService
from soc_agent.core.case_outcomes import project_soc_case_outcome
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.demo.corpus_experiments import CorpusExecutionError
from soc_agent.memory.patterns import MemoryPatternIneligibleError
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.utils.hashing import stable_hash


class CorpusRuntimeExecutor:
    def __init__(self, *, repository: SqlAlchemyAlertRepository, load_payload: Callable[[CorpusExperimentMember], dict], analysis_factory: Callable[[CorpusRound], SocAnalysisService], profile_registry: SocMemoryProfileRegistry):
        self._repository = repository
        self._load_payload = load_payload
        self._analysis_factory = analysis_factory
        self._profiles = profile_registry

    def __call__(self, round_: CorpusRound, member: CorpusExperimentMember, context: ServiceRequestContext) -> CorpusExecutionOutcome:
        payload = self._load_payload(member)
        if stable_hash(payload) != member.payload_hash:
            raise CorpusExecutionError("corpus payload no longer matches the fixed member", block_round=True)
        service = self._analysis_factory(round_)
        prior = None
        if context.idempotency_key:
            # Preserve the existing request-journal hash encoding, not the corpus hash.
            key_hash = hashlib.sha256(json.dumps(context.idempotency_key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
            prior = self._repository.find_journaled_run(alert_id=member.alert_id, input_hash=member.payload_hash, idempotency_key_hash=key_hash)
        if prior is not None and prior.status in {AnalysisRunStatus.RUNNING, AnalysisRunStatus.INTERRUPTED}:
            # Only the lease-fenced dispatcher invokes this executor. It owns the
            # alert after the prior worker's lease expired, not merely after a timeout.
            run = service.recover(AnalysisRunRecoveryCommand(run_id=prior.run_id, reason="Corpus worker reacquired the durable alert lease", stale_after_seconds=0), context=context)
        elif prior is not None and prior.request_journal.action == AuditAction.REPLAY and prior.replay_of_run_id:
            run = service.replay(prior.replay_of_run_id, context=context)
        else:
            run = service.analyze(payload, context=context)
        if run.alert_id != member.alert_id:
            raise CorpusExecutionError("Runtime returned a different alert identity", run_id=run.run_id, block_round=True)
        if run.status == AnalysisRunStatus.FAILED:
            failure = run.failure
            raise CorpusExecutionError(
                failure.message if failure else "Runtime failed", run_id=run.run_id, retryable=bool(failure and failure.retryable), block_round=bool(failure and failure.error_type in {"AuthenticationError", "PermissionDeniedError"})
            )
        if run.status == AnalysisRunStatus.RUNNING:
            raise CorpusExecutionError("prior Runtime journal is still running; recover the stale Run before retrying", run_id=run.run_id)
        observation_id = candidate_id = None
        pattern_reason = "validation_learning_disabled"
        transitions = self._repository.list_decision_transitions(run_id=run.run_id, limit=1)
        transition = transitions[0] if transitions else None
        uses = self._repository.list_memory_uses(run_id=run.run_id, limit=500)
        case = project_soc_case_outcome(run, decision_transition=transition)
        summary = {
            "analysis_status": run.status.value,
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
        if round_.selection.batch == "learning":
            if run.direct_resolution is not None:
                pattern_reason = "direct_resolution_not_an_independent_sample"
            else:
                experiment = self._repository.corpus_experiments().get_experiment(round_.experiment_id)
                service = SocMemoryPatternService(repository=self._repository, candidate_repository=self._repository, profile_registry=self._profiles, accumulation_scope=self._accumulation_scope(round_))
                try:
                    result = service.observe_run(
                        run,
                        source_type=MemoryPatternSourceType.BATCH_ALERT,
                        transport_ref=f"corpus-experiment:{round_.experiment_id}:{member.alert_id}:run:{run.run_id}",
                        environment=experiment.environment,
                        data_class=MemoryPatternDataClass.OPERATIONAL,
                        context=context,
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

    def _accumulation_scope(self, round_: CorpusRound) -> MemoryPatternAccumulationScope:
        first, last = self._repository.corpus_experiments().learning_event_range(round_.experiment_id)
        return MemoryPatternAccumulationScope(experiment_id=round_.experiment_id, configuration_hash=round_.config_hash, event_start=first, event_end=last + timedelta(microseconds=1))
