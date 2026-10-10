"""Exercise the successor workflow through the durable queue, without live models."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, members
from test_soc_corpus_experiments import context
from test_soc_corpus_rebalance_repository import _memory, repository
from test_soc_memory_patterns import _run

from soc_agent.contracts import AnalysisContextCatalogItem, AnalysisRunStatus, NormalizationAssistResult
from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.contracts.corpus_rebalance import CorpusRebalanceFact
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.demo.corpus_rebalance import CorpusRebalanceService, independent_learning_run


def setup(tmp_path):
    repo = repository(tmp_path)
    rows = [row.model_copy(update={"batch": "learning" if i < 2 else "validation", "validation_tier": None if i < 2 else "main"}) for i, row in enumerate(members(8))]
    repo.corpus_experiments().prepare(experiment(), rows)
    config = {"v": 1}

    def execute(round_, member, request):
        fact = CorpusRebalanceFact(
            alert_id=member.alert_id,
            payload_hash=member.payload_hash,
            run_id="FACT-" + member.alert_id,
            profile_id="pingan.soc",
            profile_version="13",
            feature_schema_version="v11",
            pattern_key="same-behavior",
            occurrence_key=member.alert_id,
            event_time=member.event_time,
            semantic_status="partial",
        )
        return CorpusExecutionOutcome(run_id=fact.run_id, summary={"rebalance_fact": fact.model_dump(mode="json")})

    service = SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda _: dict(config), max_concurrency=16)
    return repo, service, CorpusRebalanceService(service, SimpleNamespace()), config


def inventory(service, rebalance):
    value = rebalance.prepare_facts("EXP-test", context=context().model_copy(update={"idempotency_key": "facts-1"}))
    for round_ in value["rounds"]:
        service.start(round_["round_id"], context=context())
        while service.execute_one(round_["round_id"]):
            pass
    return value["experiment_id"]


def test_prepare_plan_apply_preserves_parent_and_is_idempotent(tmp_path):
    repo, svc, rebalance, _ = setup(tmp_path)
    parent = repo.corpus_experiments().get_experiment("EXP-test")
    facts = inventory(svc, rebalance)
    plan = rebalance.plan("EXP-test", facts, context=context())
    assert plan.counts == {"learning": 6, "validation_main": 2, "validation_supplementary": 0, "total": 8}
    assert len(plan.moved_alert_ids) == 4
    assert not repo.list_memory_pattern_observations()
    first = rebalance.apply(plan, facts, experiment_id="EXP-new", name="New", context=context())
    second = rebalance.apply(plan, facts, experiment_id="EXP-new", name="New", context=context())
    assert first == second
    assert first["imported_learning_runs"] == 0
    assert repo.corpus_experiments().get_experiment("EXP-test") == parent
    assert repo.corpus_experiments().get_experiment("EXP-new").rebalance.facts_experiment_id == facts


def test_incomplete_inventory_cannot_freeze_a_partial_report(tmp_path):
    _, svc, rebalance, _ = setup(tmp_path)
    value = rebalance.prepare_facts("EXP-test", context=context().model_copy(update={"idempotency_key": "facts-1"}))
    with pytest.raises(ValueError, match="完整"):
        rebalance.plan("EXP-test", value["experiment_id"], context=context())


@pytest.mark.parametrize("stage", ["facts", "plan", "apply", "transaction"])
@pytest.mark.parametrize("status", ["pending_review", "confirmed_candidate"])
def test_parent_pending_review_must_finish_before_successor_creation(tmp_path, monkeypatch, stage, status):
    repo, svc, rebalance, _ = setup(tmp_path)
    facts = inventory(svc, rebalance) if stage != "facts" else None
    plan = rebalance.plan("EXP-test", facts, context=context()) if stage in {"apply", "transaction"} else None

    def add_pending():
        _memory(repo, memory_id="MEM-pending", status=status)

    if stage == "transaction":
        original = rebalance.plan

        def changed_after_plan(*args, **kwargs):
            value = original(*args, **kwargs)
            add_pending()
            return value

        monkeypatch.setattr(rebalance, "plan", changed_after_plan)
    else:
        add_pending()
    with pytest.raises(ValueError, match="旧实验.*待审核"):
        if stage == "facts":
            rebalance.prepare_facts("EXP-test", context=context().model_copy(update={"idempotency_key": "blocked-facts"}))
        elif stage == "plan":
            rebalance.plan("EXP-test", facts, context=context())
        else:
            rebalance.apply(plan, facts, experiment_id="EXP-new", name="New", context=context())
    assert repo.corpus_experiments().get_experiment("EXP-new") is None


def test_client_cannot_forge_facts_or_members(tmp_path):
    _, svc, rebalance, _ = setup(tmp_path)
    facts = inventory(svc, rebalance)
    plan = rebalance.plan("EXP-test", facts, context=context())
    forged = plan.model_copy(update={"members": plan.members[:-1]})
    with pytest.raises(ValueError, match="变化"):
        rebalance.apply(forged, facts, experiment_id="EXP-forged", name="Forged", context=context())


def test_one_apply_request_cannot_create_multiple_experiments(tmp_path):
    _, svc, rebalance, _ = setup(tmp_path)
    facts = inventory(svc, rebalance)
    plan = rebalance.plan("EXP-test", facts, context=context())
    request = context().model_copy(update={"idempotency_key": "apply-once"})
    rebalance.apply(plan, facts, experiment_id="EXP-new", name="New", context=request)
    with pytest.raises(ValueError, match="Idempotency-Key"):
        rebalance.apply(plan, facts, experiment_id="EXP-another", name="Another", context=request)


def test_inventory_configuration_drift_requires_new_facts_round(tmp_path):
    _, svc, rebalance, config = setup(tmp_path)
    facts = inventory(svc, rebalance)
    config["v"] = 2
    with pytest.raises(ValueError, match="配置"):
        rebalance.plan("EXP-test", facts, context=context())


@pytest.mark.parametrize("target", ["parent", "inventory"])
def test_fact_purpose_and_inventory_cannot_be_mixed_with_learning(tmp_path, target):
    _, svc, rebalance, _ = setup(tmp_path)
    inventory_id = inventory(svc, rebalance)
    command = CorpusRoundCreateCommand(
        experiment_id="EXP-test" if target == "parent" else inventory_id,
        selection=CorpusRoundSelection(batch="learning"),
        purpose="facts" if target == "parent" else "memory",
        memory_mode="none",
        options={"normalization_review_mode": "apply"},
    )
    with pytest.raises(ValueError, match="事实"):
        svc.create_round(command, context=context())


def test_new_validation_waits_for_all_new_learning(tmp_path):
    _, svc, rebalance, _ = setup(tmp_path)
    facts = inventory(svc, rebalance)
    plan = rebalance.plan("EXP-test", facts, context=context())
    rebalance.apply(plan, facts, experiment_id="EXP-new", name="New", context=context())
    command = CorpusRoundCreateCommand(experiment_id="EXP-new", selection=CorpusRoundSelection(batch="validation", scope="all"), memory_mode="none", options={"normalization_review_mode": "apply"})
    with pytest.raises(ValueError, match="学习"):
        svc.create_round(command, context=context())
    learning = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-new", selection=CorpusRoundSelection(batch="learning"), execution_limit=100, options={"normalization_review_mode": "apply"}), context=context())
    svc.start(learning.round_id, context=context())
    while svc.execute_one(learning.round_id):
        pass
    validation = svc.create_round(command, context=context())
    svc.start(validation.round_id, context=context())
    while svc.execute_one(validation.round_id):
        pass
    assert svc.store.round_progress(validation.round_id).completed_count == 2


@pytest.mark.parametrize("defect", [None, "stub", "facts", "memory"])
def test_independent_source_excludes_memory_answers_and_fallbacks(defect):
    run = _run(1, tenant_id="pingan").model_copy(update={"model_name": "default"})
    if defect == "stub":
        run.model_name = "stub"
    elif defect == "facts":
        run.status = AnalysisRunStatus.FACTS_READY
    elif defect == "memory":
        run.llm_analysis_request.context_catalog = [AnalysisContextCatalogItem(context_ref="M-000000000001", kind="confirmed_memory", label="Memory", source_id="MEM-1", summary="known")]
    assert independent_learning_run(run) is (defect is None)


@pytest.mark.parametrize("status", ["applied", "partial", "unchanged", "failed", "skipped"])
def test_projection_uses_reviewed_facts_without_model_verdict(status):
    from soc_agent.contracts import AlertInput
    from soc_agent.demo.corpus_rebalance import project_rebalance_fact
    from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
    from soc_agent.memory.profiles import SocMemoryProfileRegistry

    run = _run(1, tenant_id="pingan")
    run.normalized_alert = AlertInput(alert_id=run.alert_id, tenant_id="pingan", event={"event_time": datetime(2026, 8, 1, tzinfo=UTC)})
    run.normalization_assistance = NormalizationAssistResult(status=status, mode="apply", request_hash="hash", model_name="fake", prompt_version="v1")
    run.analysis = None
    fact = project_rebalance_fact(run, SocMemoryProfileRegistry(profiles=[PingAnSocMemoryProfile()]), environment="dev-corpus-eval")
    assert fact.semantic_status == status
    if status in {"failed", "skipped"}:
        assert fact.pattern_key is None
