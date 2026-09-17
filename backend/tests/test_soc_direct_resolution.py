"""Direct handling must use the ordinary persisted Runtime, without a model verdict."""

import pytest
from test_soc_memory_governance import confirm
from test_soc_memory_governance import services as _services
from test_soc_pingan_memory_profile import _run as memory_sample
from test_soc_tenant_policy import _run

from soc_agent.contracts import AnalysisRun, ServiceRequestContext
from soc_agent.core.direct_resolution import SocDirectResolutionService
from soc_agent.core.runtime import analyze_alert
from soc_agent.core.tenant_policy import SocTenantPolicyEvaluationService
from soc_agent.integrations.pingan.tenant_disposition import load_pingan_tenant_disposition_policy
from soc_agent.tenant_policy import InMemoryTenantPolicyDecisionRepository, StaticTenantPolicyResolver

services = _services


def memory_case(services, *, directive=True, activate=True, selected=None, environment="prd"):
    from soc_agent.application.memory import build_soc_memory_profile_registry
    from soc_agent.contracts import AlertInput, SocMemoryRunPromotionCommand, Verdict
    from soc_agent.memory.behavior_scope import select_memory_behavior_components
    from soc_agent.memory.sources import memory_candidate_command_from_run_promotion

    service, repository = services
    request = memory_sample(1).llm_analysis_request
    payload = AlertInput(tenant_id="pingan", alert_id="direct-test", source=request.source, detection=request.detection, classification=request.classification, entities=request.canonical_entities).model_dump(mode="json")
    original = analyze_alert(payload, direct_resolution=SocDirectResolutionService(memory_service=service, profile_registry=build_soc_memory_profile_registry(), environment=environment))
    command = memory_candidate_command_from_run_promotion(original, SocMemoryRunPromotionCommand(run_id=original.run_id, metadata={"environment": environment}), profile_registry=build_soc_memory_profile_registry())
    candidate = service.propose_candidate(command)
    reviewed = select_memory_behavior_components(candidate.applicability, candidate.facets, selected, registry=build_soc_memory_profile_registry()) if selected is not None else None
    record = confirm(service, candidate, Verdict.FALSE_POSITIVE, directive=directive, record_applicability=reviewed)
    if not activate:
        record = record.model_copy(update={"retrieval_enabled": False})
        repository.save_memory_record(record)
    resolver = SocDirectResolutionService(memory_service=service, profile_registry=build_soc_memory_profile_registry(), environment=environment)
    return payload, resolver, record


def test_exact_memory_skips_main_model_and_persists_usage_not_confirmation(services):
    from soc_agent.core import SocAutomationService, SocMemoryEvolutionService
    from soc_agent.core.case_outcomes import project_soc_case_outcome

    payload, resolver, record = memory_case(services)
    repository = services[1]
    run = analyze_alert(payload, analyzer=ForbiddenModel(), role_verifier=ForbiddenModel(), direct_resolution=resolver)
    assert run.status.value == "success", run.failure
    assert run.analysis is None
    assert run.direct_resolution.source_kind == "memory"
    assert run.direct_resolution.source_id == record.memory_id
    automation = SocAutomationService(repository=repository, policy=None, environment="prd", memory_repository=repository)
    result = automation.evaluate(run, context=ServiceRequestContext())
    assert result.decision_transition.stages[0].status.value == "skipped"
    assert result.decision_transition.stages[0].after.evaluated is False
    assert not result.decision_transition.stages[0].contributors
    assert project_soc_case_outcome(run, decision_transition=result.decision_transition).recommended_handling == "ignore"
    evolution = SocMemoryEvolutionService(repository=repository, memory_record_repository=repository, automation_repository=repository)
    uses = evolution.capture_run_usage(run)
    assert len(uses) == 1
    assert uses[0].effect.value == "direct_reused"
    assert not uses[0].base_model_evaluated
    assert len(evolution.capture_run_usage(run)) == 1
    assert not repository.list_memory_feedback(memory_id=record.memory_id)

    from soc_agent.demo.corpus_workbench import _audit_bundle, _execution_view

    execution = _execution_view(alert_id=run.alert_id, run=run, observation=None, replay=None, candidate=None)
    decision = next(phase for phase in execution.phases if phase.phase == "decision")
    assert decision.metrics["processing_path"] == "审核经验直接复用"
    assert decision.metrics["memory_id"] == record.memory_id
    assert decision.metrics["verdict"] == "false_positive"
    assert "matched_rule" not in decision.metrics
    audit = _audit_bundle(run=run, execution=execution, observation=None, replay=None, candidates=[], memory_records=[], review_items=[], summary=None, decision_transitions=[], memory_uses=[])
    assert next(item for item in audit.artifacts if item.phase == "decision").metrics["disposition"] == "忽略"


@pytest.mark.parametrize("directive,activate", [(False, True), (True, False)])
def test_context_only_or_paused_memory_does_not_skip_model(services, directive, activate):
    payload, resolver, _ = memory_case(services, directive=directive, activate=activate)
    run = analyze_alert(payload, direct_resolution=resolver)
    assert run.direct_resolution is None
    assert run.analysis is not None


def test_direct_memory_can_be_corrected_without_overwriting_the_adopted_snapshot(services):
    from test_soc_memory_revision_workflow import _reviewer_context

    from soc_agent.contracts import CorrectionCommand, Verdict
    from soc_agent.core import SocReviewService

    payload, resolver, _ = memory_case(services)
    run = analyze_alert(payload, analyzer=ForbiddenModel(), direct_resolution=resolver)
    repository = services[1]
    repository.save_run(run)
    review = SocReviewService(repository=repository, summary_repository=repository, audit_repository=repository)
    context = _reviewer_context(key="direct-correction")
    context.actor.roles.append("soc_analyst")
    corrected = review.correct(CorrectionCommand(run_id=run.run_id, corrected_verdict=Verdict.TRUE_POSITIVE, reason="模拟运营发现新反证，纠正本次复用结论。"), context=context)
    assert corrected.decision.verdict is Verdict.TRUE_POSITIVE
    assert corrected.direct_resolution.decision.verdict is Verdict.FALSE_POSITIVE
    assert corrected.corrections[0].previous_verdict is Verdict.FALSE_POSITIVE
    assert repository.get_run(run.run_id).corrections[0].reason == corrected.corrections[0].reason


def test_direct_handling_does_not_create_independent_pattern_support(services):
    from soc_agent.application.memory import build_soc_memory_profile_registry
    from soc_agent.contracts import MemoryPatternDataClass
    from soc_agent.core import SocMemoryPatternPostAnalysisObserver, SocMemoryPatternService

    payload, resolver, _ = memory_case(services)
    payload["event"] = {"event_time": "2026-09-16T08:00:00Z"}
    run = analyze_alert(payload, analyzer=ForbiddenModel(), direct_resolution=resolver)
    service = SocMemoryPatternService(repository=services[1], candidate_repository=services[1], profile_registry=build_soc_memory_profile_registry())
    observer = SocMemoryPatternPostAnalysisObserver(service=service, environment="prd", data_class=MemoryPatternDataClass.SIMULATION)
    observer.observe(run, context=ServiceRequestContext())
    observations = services[1].list_memory_pattern_observations(limit=10)
    assert len(observations) == 1
    assert observations[0].lesson is None
    assert observations[0].metadata["conclusion_origin"] == "memory_reuse"
    assert observations[0].schema_version == "soc.memory_pattern_observation.v4"


def test_corpus_direct_memory_keeps_factual_observation_and_trace(services, tmp_path, monkeypatch):
    import pandas as pd
    from test_soc_corpus_workbench import _admin_context

    from soc_agent.application.memory import build_soc_memory_profile_registry
    from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService, SocMemoryPatternService
    from soc_agent.demo.corpus_workbench import CORPUS_WORKBENCH_ENVIRONMENT, SocCorpusWorkbenchService
    from soc_agent.llm import SocAnalyzerMode, SocLLMSettings

    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")
    payload, resolver, _ = memory_case(services, environment=CORPUS_WORKBENCH_ENVIRONMENT)
    payload["event"] = {"event_time": "2026-09-16T08:00:00Z"}
    repository = services[1]
    source = tmp_path / "direct-memory-corpus.pkl"
    with pd.option_context("future.infer_string", False):
        pd.DataFrame([{"alert_id": payload["alert_id"], "alert_full_data": {"alert_data": payload}}], dtype=object).to_pickle(source)
    workbench = SocCorpusWorkbenchService(
        repository=repository,
        analysis_service=SocAnalysisService(repository=repository, runtime=DeterministicAnalysisRuntime(analyzer=ForbiddenModel(), direct_resolution=resolver)),
        pattern_service=SocMemoryPatternService(repository=repository, candidate_repository=repository, profile_registry=build_soc_memory_profile_registry()),
        source_path=source,
        settings=SocLLMSettings(mode=SocAnalyzerMode.STUB),
        database_file="direct-memory.sqlite",
    )
    result = workbench.process_alert(payload["alert_id"], context=_admin_context("direct-memory-workbench", actor_id="reviewer"))
    assert repository.get_run(result.run_id).direct_resolution.source_kind == "memory"
    assert result.observation_id is not None
    observation = repository.get_memory_pattern_observation(result.observation_id)
    assert observation.lesson is None
    assert observation.metadata["conclusion_origin"] == "memory_reuse"
    execution = workbench.get_execution(payload["alert_id"])
    phase = next(p for p in execution.phases if p.phase == "memory")
    assert phase.status == "success"
    assert "事实观察" in phase.summary
    assert phase.metrics["independent_confirmation_added"] is False
    assert phase.metrics["observation_id"] == observation.observation_id
    assert execution.status == "completed"
    replay = workbench.process_alert(payload["alert_id"], context=_admin_context("direct-memory-again", actor_id="reviewer"))
    assert replay.observation_id == result.observation_id


def test_fixed_workbench_forwards_direct_memory_to_pattern_service(services):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from test_soc_corpus_workbench import _admin_context

    from soc_agent.demo.memory_workbench import SocMemoryWorkbenchService

    payload, resolver, _ = memory_case(services)
    run = analyze_alert(payload, analyzer=ForbiddenModel(), direct_resolution=resolver)
    observe = Mock(side_effect=RuntimeError("pattern-observer-reached"))
    workbench = SimpleNamespace(
        _cases={run.alert_id: SimpleNamespace(payload=payload)},
        get_state=lambda: SimpleNamespace(alerts=[SimpleNamespace(alert_id=run.alert_id, workflow_state="ready")], progress=SimpleNamespace(next_alert_id=run.alert_id)),
        _analysis_idempotency_key=lambda _: "fixed-direct-memory",
        _analysis_service=SimpleNamespace(analyze=lambda *args, **kwargs: run),
        _pattern_service=SimpleNamespace(observe_run=observe),
        _source_sha256="a" * 64,
    )
    with pytest.raises(RuntimeError, match="pattern-observer-reached"):
        SocMemoryWorkbenchService.process_alert(workbench, run.alert_id, context=_admin_context("fixed-direct", actor_id="reviewer"))
    assert observe.call_args.args[0] is run


def test_policy_has_priority_over_memory_inventory():
    class NoMemory:
        def find_directive_records(self, query):
            raise AssertionError("policy hit must precede Memory")

    resolver = SocDirectResolutionService(tenant_policy_service=policy_service(), memory_service=NoMemory(), environment="dev")
    run = analyze_alert(_run(rule_code="RPAADM_000558").input_payload, analyzer=ForbiddenModel(), direct_resolution=resolver)
    assert run.direct_resolution.source_kind == "tenant_policy", run.failure


def test_policy_direct_result_is_visible_in_workbench_list_and_trace(tmp_path, monkeypatch):
    import pandas as pd
    from test_soc_corpus_workbench import _admin_context, _repository

    from soc_agent.application.analysis import build_soc_analysis_service
    from soc_agent.application.memory import build_soc_memory_profile_registry
    from soc_agent.core import SocMemoryPatternService
    from soc_agent.demo.corpus_workbench import CORPUS_WORKBENCH_ENVIRONMENT, SocCorpusWorkbenchService
    from soc_agent.integrations.pingan.tenant_disposition import PINGAN_TENANT_DISPOSITION_POLICY_PATH
    from soc_agent.llm import SocAnalyzerMode, SocLLMSettings

    monkeypatch.setenv("SOC_DIRECT_RESOLUTION_ENABLED", "true")
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")
    monkeypatch.setenv("SOC_TENANT_POLICY_ENABLED", "true")
    monkeypatch.setenv("SOC_TENANT_DISPOSITION_POLICY_PATH", str(PINGAN_TENANT_DISPOSITION_POLICY_PATH))
    monkeypatch.setenv("SOC_TENANT_POLICY_ADVISOR_MODE", "off")
    monkeypatch.delenv("SOC_TENANT_POLICY_SKILL_PATH", raising=False)
    repository = _repository(tmp_path)
    payload = _run(rule_code="RPAADM_000558").input_payload
    alert_id = payload["alert_id"]
    source = tmp_path / "policy-corpus.pkl"
    with pd.option_context("future.infer_string", False):
        pd.DataFrame([{"alert_id": alert_id, "alert_full_data": {"alert_data": payload}}], dtype=object).to_pickle(source)
    settings = SocLLMSettings(mode=SocAnalyzerMode.STUB)
    analysis = build_soc_analysis_service(repository, settings=settings, runtime_environment=CORPUS_WORKBENCH_ENVIRONMENT, pattern_observation_enabled=False)
    workbench = SocCorpusWorkbenchService(
        repository=repository,
        analysis_service=analysis,
        pattern_service=SocMemoryPatternService(repository=repository, candidate_repository=repository, profile_registry=build_soc_memory_profile_registry()),
        source_path=source,
        settings=settings,
        database_file="soc-corpus-workbench.sqlite",
    )
    context = _admin_context("policy-workbench-run", actor_id="scope-test")
    result = workbench.process_alert(alert_id, context=context)
    saved = repository.get_run(result.run_id)
    assert saved.status.value == "success", saved.failure
    assert saved.direct_resolution.source_kind == "tenant_policy"
    assert saved.analysis is None
    assert saved.llm_analysis_request.environment == CORPUS_WORKBENCH_ENVIRONMENT
    assert result.alert.run_id == saved.run_id
    assert result.alert.workflow_state == "completed"
    assert result.observation_id is None
    execution = workbench.get_execution(alert_id)
    assert execution.run_id == saved.run_id
    assert execution.status == "completed"
    memory_phase = next(phase for phase in execution.phases if phase.phase == "memory")
    assert memory_phase.status == "skipped"
    assert all(step.status == "skipped" for step in memory_phase.steps)
    assert "未检索或复用 Memory" in memory_phase.summary
    semantic = next(phase for phase in execution.phases if phase.phase == "semantic_review")
    assert semantic.status == "skipped"
    assert "企业规则" in semantic.summary
    assert semantic.steps == []
    assert semantic.metrics == {}
    assert saved.normalization_assistance is None
    decision = next(phase for phase in execution.phases if phase.phase == "decision")
    assert "平安明确规则码转交" in decision.summary
    assert "直接转交" in decision.summary
    assert decision.metrics["rule_code"] == "RPAADM_000558"
    assert decision.metrics["disposition"] == "转交"
    assert "verdict" not in decision.metrics
    assert "confidence" not in decision.metrics
    assert "evidence_state" not in decision.metrics
    assert next(phase for phase in execution.phases if phase.phase == "context").metrics == {}

    from soc_agent.demo.corpus_workbench import _audit_bundle

    before = saved.model_dump_json()
    audit = _audit_bundle(run=saved, execution=execution, observation=None, replay=None, candidates=[], memory_records=[], review_items=[], summary=None, decision_transitions=[], memory_uses=[])
    semantic_artifact = next(item for item in audit.artifacts if item.phase == "semantic_review")
    assert semantic_artifact.status == "skipped"
    assert "已跳过" in semantic_artifact.title
    assert semantic_artifact.normalization_review is None
    assert semantic_artifact.payload["result"] is None
    decision_artifact = next(item for item in audit.artifacts if item.phase == "decision")
    assert "confidence" not in decision_artifact.metrics
    assert decision_artifact.payload["base_decision"]["confidence"] is None
    assert decision_artifact.payload["direct_resolution"]["selected_rule_id"] == saved.direct_resolution.selected_rule_id
    assert saved.model_dump_json() == before

    assert workbench.get_activity().active_count == 0
    assert analysis.analyze(payload, context=context.model_copy(update={"idempotency_key": workbench._analysis_idempotency_key(alert_id)})).run_id == saved.run_id

    # A newer result in another environment must not replace this workbench result.
    other = build_soc_analysis_service(repository, settings=settings, runtime_environment="stg", pattern_observation_enabled=False).analyze(payload)
    assert other.llm_analysis_request.environment == "stg"
    state = workbench.get_state(search=alert_id, unprocessed_only=False)
    assert state.alerts[0].run_id == saved.run_id
    assert state.alerts[0].workflow_state == "completed"
    assert workbench.get_execution(alert_id).run_id == saved.run_id

    # Recover the early direct-policy format from its frozen server policy scope.
    saved.llm_analysis_request.environment = None
    repository.save_run(saved)
    assert workbench.get_state(search=alert_id, unprocessed_only=False).alerts[0].run_id == saved.run_id
    assert workbench.get_execution(alert_id).run_id == saved.run_id
    other.llm_analysis_request.environment = None
    repository.save_run(other)
    assert workbench.get_state(search=alert_id, unprocessed_only=False).alerts[0].run_id == saved.run_id
    assert workbench.get_execution(alert_id).run_id == saved.run_id


def test_shadow_policy_does_not_short_circuit():
    from soc_agent.contracts import TenantPolicyMode

    policy = load_pingan_tenant_disposition_policy().model_copy(update={"policy_mode": TenantPolicyMode.SHADOW})
    # Construct through validation so enum identity and mode constraints remain enforced.
    policy = type(policy).model_validate(policy.model_dump(mode="json"))
    run = analyze_alert(_run(rule_code="RPAADM_000558").input_payload, direct_resolution=SocDirectResolutionService(tenant_policy_service=policy_service(policy), environment="dev"))
    assert run.direct_resolution is None
    assert run.analysis is not None


@pytest.mark.parametrize("environment,snapshot", [(None, None), (None, {}), (None, {"environment": "stg"}), ("stg", {"environment": "dev-corpus-eval"})])
def test_workbench_policy_scope_recovery_never_guesses_missing_or_foreign_scope(environment, snapshot):
    from types import SimpleNamespace

    from soc_agent.demo.corpus_workbench import _matches_corpus_run

    run = analyze_alert(_run(rule_code="RPAADM_000558").input_payload, direct_resolution=SocDirectResolutionService(tenant_policy_service=policy_service(), environment="dev"))
    run.llm_analysis_request.environment = environment
    run.direct_resolution = run.direct_resolution.model_copy(update={"policy_snapshot": snapshot})
    assert not _matches_corpus_run(run, SimpleNamespace(payload_hash=run.input_hash))


def test_higher_priority_model_condition_is_deferred_not_skipped():
    from soc_agent.contracts import Verdict

    policy = load_pingan_tenant_disposition_policy()
    forced = next(rule for rule in policy.rules if "RPAADM_000558" in rule.match.rule_codes)
    higher = forced.model_copy(update={"rule_id": "higher-model-dependent", "priority": 1, "match": forced.match.model_copy(update={"detection_verdicts": [Verdict.TRUE_POSITIVE]})})
    policy = policy.model_copy(update={"rules": [higher, forced]})
    run = analyze_alert(_run(rule_code="RPAADM_000558").input_payload, direct_resolution=SocDirectResolutionService(tenant_policy_service=policy_service(policy), environment="dev"))
    assert run.analysis is not None, run.failure
    assert run.direct_resolution is None
    check = next(step for step in run.steps if step.step_name == "direct_policy")
    assert check.metadata["policy_precheck"]["status"] == "deferred"


def test_direct_memory_inventory_conflict_is_not_hidden_by_top_k(services, monkeypatch):
    from soc_agent.contracts import Verdict

    payload, resolver, record = memory_case(services)
    service = services[0]
    retrieve = service.find_directive_records

    def conflicting(query):
        result = retrieve(query)
        match = result.matches[0]
        opposite = record.model_copy(update={"memory_id": "MEM-OPPOSITE", "decision_directive": record.decision_directive.model_copy(update={"target_verdict": Verdict.TRUE_POSITIVE})})
        return result.model_copy(update={"matches": [match] * query.limit + [match.model_copy(update={"record": opposite, "memory_id": opposite.memory_id})]})

    monkeypatch.setattr(service, "find_directive_records", conflicting)
    run = analyze_alert(payload, direct_resolution=resolver)
    assert run.direct_resolution is None
    assert run.analysis is not None
    gate = next(step for step in run.steps if step.step_name == "direct_memory")
    assert gate.metadata["target_verdicts"] == ["false_positive", "true_positive"]
    from soc_agent.core import SocAutomationService

    assert run.direct_memory_conflicted
    evaluation = SocAutomationService(repository=services[1], policy=None, environment="prd", memory_repository=services[1]).evaluate(run, context=ServiceRequestContext())
    assert evaluation.decision_transition.transition_kind.value == "conflicted"
    assert evaluation.effective_disposition is None


def test_unavailable_memory_falls_back_to_normal_analysis(services, monkeypatch):
    payload, resolver, _ = memory_case(services)

    def unavailable(query):
        raise OSError("simulated store unavailable")

    monkeypatch.setattr(services[0], "find_directive_records", unavailable)
    run = analyze_alert(payload, direct_resolution=resolver)
    assert run.analysis is not None
    assert run.direct_resolution is None


def test_direct_reuse_honors_selected_behaviors_not_the_original_full_hash(services):
    from soc_agent.application.memory import build_soc_memory_profile_registry
    from soc_agent.memory.retrieval import memory_query_from_analysis_request

    payload, resolver, record = memory_case(services, selected=["technique:t1059"])
    payload["classification"]["technique"] = ["T1059"]
    run = analyze_alert(payload, analyzer=ForbiddenModel(), direct_resolution=resolver)
    assert run.direct_resolution is not None, run.failure
    profiles = build_soc_memory_profile_registry()
    query = memory_query_from_analysis_request(run.llm_analysis_request, profile=profiles.resolve_request(run.llm_analysis_request))
    assert query.facets["behavior_fingerprint"] != record.facets["behavior_fingerprint"]


def test_direct_policy_uses_common_persistence_lineage_and_zero_call_metrics(services):
    from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService, SocAutomationService
    from soc_agent.core.case_outcomes import project_soc_case_outcome
    from soc_agent.db.repositories import _run_effectiveness_projection

    repository = services[1]
    policy = SocTenantPolicyEvaluationService(policy_resolver=StaticTenantPolicyResolver([load_pingan_tenant_disposition_policy()]), repository=repository, environment="dev", event_timezone="Asia/Shanghai")
    automation = SocAutomationService(repository=repository, policy=None, environment="dev", tenant_policy_repository=repository)
    service = SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(analyzer=ForbiddenModel(), direct_resolution=SocDirectResolutionService(tenant_policy_service=policy, environment="dev")),
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        analysis_persistence=repository,
        post_analysis_observers=[policy, automation],
    )
    run = service.analyze(_run(rule_code="RPAADM_000558").input_payload, context=ServiceRequestContext(idempotency_key="direct-policy-test"))
    assert repository.get_run(run.run_id).direct_resolution is not None
    assert repository.get_alert_summary(run.run_id).summary
    transitions = repository.list_decision_transitions(run_id=run.run_id)
    assert len(transitions) == 1
    stages = transitions[0].stages
    assert stages[1].before == stages[1].after
    assert stages[2].status.value == "applied"
    assert stages[2].source_hash == run.direct_resolution.source_hash
    assert stages[2].selected_rule_id == run.direct_resolution.selected_rule_id
    outcome = project_soc_case_outcome(run, decision_transition=transitions[0])
    assert outcome.decision_usable
    assert outcome.recommended_handling == "transfer"
    assert outcome.processing_path == "tenant_policy"
    assert not outcome.evidence_gaps
    assert all(item.kind.value != "current_analysis" for item in outcome.basis)
    assert _run_effectiveness_projection(run)["provider_call_count"] == 0
    assert service.analyze(run.input_payload, context=ServiceRequestContext(idempotency_key="direct-policy-test")).run_id == run.run_id


@pytest.mark.parametrize("enabled", [True, False])
def test_application_composition_owns_the_rollback_switch(monkeypatch, enabled):
    from soc_agent.application.analysis import build_soc_analysis_service
    from soc_agent.llm import SocLLMSettings

    monkeypatch.setenv("SOC_DIRECT_RESOLUTION_ENABLED", str(enabled).lower())
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")
    monkeypatch.setenv("SOC_TENANT_POLICY_ENABLED", "false")
    monkeypatch.setenv("SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED", "false")
    service = build_soc_analysis_service(settings=SocLLMSettings(mode="stub"), runtime_environment="dev")
    assert (service._runtime._direct_resolution is not None) is enabled


class ForbiddenModel:
    model_name = "must-not-call"
    prompt_version = "test"
    step_name = "analyze_llm"

    def analyze(self, request):
        raise AssertionError("main model must not run")

    def prepare(self, alert):
        raise AssertionError("semantic review must not run")


def policy_service(policy=None):
    return SocTenantPolicyEvaluationService(
        policy_resolver=StaticTenantPolicyResolver([policy or load_pingan_tenant_disposition_policy()]),
        repository=InMemoryTenantPolicyDecisionRepository(),
        environment="dev",
        event_timezone="Asia/Shanghai",
    )


@pytest.mark.parametrize("code,status,expected", [("RPAADM_000558", 200, "escalated"), ("OTHER", 403, "ignored")])
def test_policy_direct_path_skips_every_model(code, status, expected):
    resolver = SocDirectResolutionService(tenant_policy_service=policy_service(), environment="dev")
    run = analyze_alert(_run(rule_code=code, status_code=status).input_payload, analyzer=ForbiddenModel(), normalization_reviewer=ForbiddenModel(), direct_resolution=resolver)
    assert run.status.value == "success", run.failure
    assert run.analysis is None
    assert run.normalization_assistance is None
    assert run.decision.confidence is None
    assert run.direct_resolution.source_kind == "tenant_policy"
    assert run.direct_resolution.disposition.value == expected
    assert run.direct_resolution.policy_snapshot["detection_truth"]["source"] == "not_evaluated"
    assert not run.provider_request_journals
    assert run.total_duration_ms is not None
    assert AnalysisRun.model_validate_json(run.model_dump_json()).direct_resolution == run.direct_resolution


def test_direct_policy_survives_ordinary_result_and_legacy_projection():
    from soc_agent.core.case_outcomes import project_soc_case_outcome
    from soc_agent.integrations.pingan.legacy_compat.result_mapper import PingAnLegacyResultMapper

    policy = policy_service()
    run = analyze_alert(_run(rule_code="RPAADM_000558").input_payload, analyzer=ForbiddenModel(), direct_resolution=SocDirectResolutionService(tenant_policy_service=policy, environment="dev"))
    policy.observe(run, context=ServiceRequestContext())
    outcome = project_soc_case_outcome(run)
    assert outcome.recommended_handling == "transfer"
    assert outcome.decision_usable
    assert outcome.base_verdict is None
    assert outcome.confidence is None
    assert not outcome.evidence_gaps
    result = PingAnLegacyResultMapper().project(run, decision_transitions=[], action_executions=[])
    assert result["alert_action"] == "转交"
    assert result["soc_lineage"]["base_verdict"] is None
