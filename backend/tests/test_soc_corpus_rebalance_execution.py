"""The successor uses real durable jobs and Pattern accumulation with fake models."""

from collections import Counter
from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, members
from test_soc_corpus_experiments import context
from test_soc_corpus_rebalance_repository import _memory, repository
from test_soc_memory_patterns import _run

from soc_agent.contracts import AlertInput, AnalysisContextCatalogItem, AnalysisRunStatus, NormalizationAssistResult, Verdict
from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.contracts.corpus_rebalance import CorpusRebalanceFact
from soc_agent.demo.corpus_experiment_runtime import CorpusRuntimeExecutor
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.demo.corpus_rebalance import CorpusRebalanceService, learning_run_hash
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.utils.hashing import stable_hash


def _fixture(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    rows = []
    templates, payloads = {}, {}
    for index, original in enumerate(members(8)):
        run = _run(index, tenant_id="pingan")
        run.alert_id = original.alert_id
        run.input_payload = {"alert_id": run.alert_id, "event_time": original.event_time.isoformat()}
        run.input_hash = stable_hash(run.input_payload)
        run.started_at = original.event_time
        run.model_name = "fixture-model"
        run.prompt_version = "fixture-analysis-v1"
        run.llm_analysis_request.alert_id = run.alert_id
        run.llm_analysis_request.environment = "dev-corpus-eval"
        run.normalized_alert = AlertInput(alert_id=run.alert_id, tenant_id="pingan", source=run.llm_analysis_request.source, detection=run.llm_analysis_request.detection, event={"event_time": original.event_time})
        run.normalization_assistance = NormalizationAssistResult(mode="apply", status="partial", request_hash=stable_hash(["review", run.input_hash]), model_name="fixture-reviewer", prompt_version="fixture-semantic-v1")
        templates[run.alert_id] = run
        payloads[run.alert_id] = run.input_payload
        rows.append(original.model_copy(update={"payload_hash": run.input_hash, "batch": "learning" if index < 2 else "validation", "validation_tier": None if index < 2 else "main"}))
    repo.corpus_experiments().prepare(experiment(), rows)
    calls = []
    cached = {}

    def project(run, profiles, *, environment):
        # Keep the real saved Run identities/hashes while avoiding unrelated
        # vendor parsing fixtures; Pattern accumulation below uses the real
        # Generic profile and its independent experiment scope.
        profile = profiles.resolve_run(run).identity
        return CorpusRebalanceFact(
            alert_id=run.alert_id,
            payload_hash=run.input_hash,
            run_id=run.run_id,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            feature_schema_version=profile.feature_schema_version,
            pattern_key="reviewed-detector-and-core",
            occurrence_key=stable_hash(["occurrence", run.alert_id]),
            event_time=run.normalized_alert.event.event_time,
            semantic_status=run.normalization_assistance.status,
        )

    monkeypatch.setattr("soc_agent.demo.corpus_rebalance.project_rebalance_fact", project)
    monkeypatch.setattr("soc_agent.demo.corpus_experiment_runtime.project_rebalance_fact", project)

    def factory(round_):
        def analyze(payload, *, context):
            if context.idempotency_key in cached:
                return cached[context.idempotency_key]
            run = templates[payload["alert_id"]].model_copy(deep=True)
            run.run_id = "RUN-" + stable_hash([round_.round_id, run.alert_id])[:24]
            calls.append((round_.experiment_id, round_.purpose, round_.selection.batch, run.alert_id))
            if round_.purpose == "facts":
                run.status, run.analysis, run.decision = AnalysisRunStatus.FACTS_READY, None, None
                run.pipeline_version = "soc-runtime-v1-facts"
                run.model_name = "not_invoked"
            elif round_.experiment_id == "EXP-test" and round_.selection.batch == "validation":
                run.analysis.verdict = Verdict.FALSE_POSITIVE
            repo.save_run(run)
            cached[context.idempotency_key] = run
            return run

        return SimpleNamespace(analyze=analyze)

    profiles = SocMemoryProfileRegistry()
    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda member: payloads[member.alert_id], analysis_factory=factory, profile_registry=profiles)
    service = SocCorpusExperimentService(repository=repo, execute=executor, configuration_provider=lambda options: {"fake_model": "fixture-model", "normalization": options.normalization_review_mode}, max_concurrency=16)
    return SimpleNamespace(repo=repo, service=service, rebalance=CorpusRebalanceService(service, profiles), calls=calls, rows=rows)


def _round(fixture, experiment_id, batch, **selection):
    return fixture.service.create_round(
        CorpusRoundCreateCommand(
            experiment_id=experiment_id,
            selection=CorpusRoundSelection(batch=batch, scope="reuse" if batch == "learning" else "all", **selection),
            options={"normalization_review_mode": "apply"},
            execution_limit=100,
            concurrency=16,
            memory_mode="none" if batch == "learning" or experiment_id == "EXP-test" else "snapshot",
        ),
        context=context(),
    )


def _finish(fixture, round_):
    fixture.service.start(round_.round_id, context=context())
    for _ in range(30):
        if not fixture.service.execute_one(round_.round_id):
            break
    else:
        pytest.fail("fixture queue did not drain")
    progress = fixture.service.store.round_progress(round_.round_id)
    assert progress.round.state == "completed", progress.model_dump(mode="json")
    assert progress.failed_count == 0
    return fixture.service.store.list_round_items(round_.round_id, limit=100).items


def _parent_and_successor(fixture, *, transform_old=None):
    old_learning = _finish(fixture, _round(fixture, "EXP-test", "learning"))
    old_validation = _finish(fixture, _round(fixture, "EXP-test", "validation", alert_ids=["2", "3"]))
    if transform_old:
        for item in old_learning:
            run = fixture.repo.get_run(item.job.run_id)
            transform_old(run)
            fixture.repo.save_run(run)
    _memory(fixture.repo)
    before = fixture.repo.list_memory_pattern_observations(limit=100)
    inventory = fixture.rebalance.prepare_facts("EXP-test", context=context().model_copy(update={"idempotency_key": "facts-full-flow"}))
    for value in inventory["rounds"]:
        items = _finish(fixture, fixture.service.store.get_round(value["round_id"]))
        assert all(item.job.result_payload["pattern_reason"] == "facts_only_no_learning" for item in items)
        assert all(fixture.repo.get_run(item.job.run_id).status == AnalysisRunStatus.FACTS_READY for item in items)
    assert fixture.repo.list_memory_pattern_observations(limit=100) == before
    plan = fixture.rebalance.plan("EXP-test", inventory["experiment_id"], context=context())
    applied = fixture.rebalance.apply(plan, inventory["experiment_id"], experiment_id="EXP-successor", name="Successor", context=context().model_copy(update={"idempotency_key": "apply-full-flow"}))
    return old_learning, old_validation, plan, applied


def test_existing_learning_is_imported_and_both_batches_accumulate_within_their_experiment(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    old_learning, old_validation, plan, applied = _parent_and_successor(fixture)
    assert applied["imported_learning_runs"] == 2 and applied["inherited_memories"] == 1
    assert plan.counts == {"learning": 6, "validation_main": 2, "validation_supplementary": 0, "total": 8}
    original_hashes = {item.alert_id: learning_run_hash(fixture.repo.get_run(item.job.run_id)) for item in old_learning}
    call_count = len(fixture.calls)
    learned = _finish(fixture, _round(fixture, "EXP-successor", "learning"))
    assert [call[-1] for call in fixture.calls[call_count:]] == ["2", "3", "4", "5"]
    assert [item.job.result_payload["summary"]["pattern_support_count"] for item in learned] == [1, 2, 3, 4, 5, 6]
    imported = {item.alert_id: item for item in learned[:2]}
    for original in old_learning:
        child = imported[original.alert_id]
        assert child.job.run_id == original.job.run_id
        summary = child.job.result_payload["summary"]
        assert summary["inherited_from_run_id"] == original.job.run_id
        assert summary["measurements"] == {} and summary["total_duration_ms"] == 0 and summary["base_model_evaluated"] is False
        assert learning_run_hash(fixture.repo.get_run(original.job.run_id)) == original_hashes[original.alert_id]
    moved = {item.alert_id: item for item in learned[2:]}
    for original in old_validation:
        assert moved[original.alert_id].job.run_id != original.job.run_id
        assert fixture.repo.get_run(original.job.run_id).analysis.verdict == Verdict.FALSE_POSITIVE
        assert fixture.repo.get_run(moved[original.alert_id].job.run_id).analysis.verdict == Verdict.SUSPICIOUS
    observations = fixture.repo.list_memory_pattern_observations(limit=100)
    # The parent retains its two learning and two validation observations;
    # the successor independently accumulates its six learning members.
    assert Counter(row.accumulation_scope.experiment_id for row in observations) == {"EXP-test": 4, "EXP-successor": 6}
    assert {row.source.alert_id for row in observations if row.accumulation_scope.experiment_id == "EXP-test"} == {"0", "1", "2", "3"}
    candidate = fixture.repo.get_memory_candidate(learned[-1].job.result_payload["candidate_id"])
    assert candidate.source.metadata["experiment_id"] == "EXP-successor"
    validation = _round(fixture, "EXP-successor", "validation")
    assert [entry.memory_id for entry in validation.memory_snapshot] == ["MEM-origin"]
    frozen_memory = fixture.repo.get_memory_record("MEM-origin")
    validated = _finish(fixture, validation)
    assert {item.alert_id for item in validated} == {"6", "7"}
    assert [item.job.result_payload["summary"]["pattern_support_count"] for item in validated] == [7, 8]
    after = fixture.repo.list_memory_pattern_observations(limit=100)
    assert Counter(row.accumulation_scope.experiment_id for row in after) == {"EXP-test": 4, "EXP-successor": 8}
    child_observations = [row for row in after if row.accumulation_scope.experiment_id == "EXP-successor"]
    assert {row.source.alert_id for row in child_observations} == {str(i) for i in range(8)}
    assert len({row.aggregation_key for row in child_observations}) == 1
    # A snapshot alone is not a dependency: this fixture's analyzer did not
    # receive Memory context, so its two new conclusions remain independent.
    assert all(row.lesson is not None for row in child_observations)
    assert all(fixture.repo.get_memory_pattern_observation(row.observation_id) == row for row in observations)
    assert fixture.service.store.get_round(validation.round_id).memory_snapshot == validation.memory_snapshot
    assert fixture.repo.get_memory_record("MEM-origin") == frozen_memory
    assert fixture.repo.corpus_experiments().get_experiment("EXP-test").member_count == 8


def test_import_hash_change_after_plan_freeze_blocks_before_a_new_model_call(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    old_learning, _, _, _ = _parent_and_successor(fixture)
    changed = fixture.repo.get_run(old_learning[0].job.run_id)
    changed.analysis.summary = "Changed after successor preparation"
    fixture.repo.save_run(changed)
    round_ = _round(fixture, "EXP-successor", "learning")
    before = list(fixture.calls)
    fixture.service.start(round_.round_id, context=context())
    assert fixture.service.execute_one(round_.round_id)
    progress = fixture.service.store.round_progress(round_.round_id)
    assert progress.round.state == "blocked"
    item = fixture.service.store.list_round_items(round_.round_id).items[0]
    assert "changed after the plan was frozen" in item.job.error_message
    assert fixture.calls == before


def test_old_learning_that_used_memory_is_reanalyzed_instead_of_imported(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)

    def make_memory_dependent(run):
        run.llm_analysis_request.context_catalog = [AnalysisContextCatalogItem(context_ref="M-000000000001", kind="confirmed_memory", label="Memory", source_id="MEM-existing", summary="reference")]

    _, _, _, applied = _parent_and_successor(fixture, transform_old=make_memory_dependent)
    assert applied["imported_learning_runs"] == 0
    before = len(fixture.calls)
    learned = _finish(fixture, _round(fixture, "EXP-successor", "learning"))
    assert [call[-1] for call in fixture.calls[before:]] == ["0", "1", "2", "3", "4", "5"]
    assert all("inherited_from_run_id" not in item.job.result_payload["summary"] for item in learned)
