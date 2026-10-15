"""Both corpus batches feed one governed stream of distinct alert behaviors."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context, reviewed_memory
from test_soc_memory_patterns import _run

from soc_agent.contracts import (
    AlertEntitySet,
    AnalysisContextCatalogItem,
    AnalysisReasoningBasis,
    Decision,
    ProcessEntityRef,
    SocDirectResolution,
    Verdict,
)
from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiment_runtime import CorpusRuntimeExecutor
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.utils.hashing import stable_hash


def _fixture(tmp_path, *, fifth_origin="independent", fifth_process="cmd.exe", frozen_memory=False):
    repo = repository(tmp_path)
    rows, runs, payloads = [], {}, {}
    for index, template in enumerate(members(6)):
        observed = datetime(2026, 8, 6, tzinfo=UTC) + timedelta(days=index)
        row = template.model_copy(
            update={
                "batch": "learning" if index < 4 else "validation",
                "validation_tier": None if index < 4 else "main",
                "event_time": observed,
            }
        )
        run = _run(index, tenant_id="pingan", detection_key=None, verdict=Verdict.FALSE_POSITIVE)
        run.alert_id = row.alert_id
        run.input_payload = {"alert_id": row.alert_id, "event_time": observed.isoformat()}
        run.input_hash = stable_hash(run.input_payload)
        run.llm_analysis_request.alert_id = row.alert_id
        run.llm_analysis_request.environment = "dev-corpus-eval"
        run.llm_analysis_request.classification.technique = ["T1059"]
        run.llm_analysis_request.canonical_entities = AlertEntitySet(process=ProcessEntityRef(process_name=fifth_process if index == 4 else "cmd.exe"))
        runs[row.alert_id] = run
        payloads[row.alert_id] = run.input_payload
        rows.append(row.model_copy(update={"payload_hash": run.input_hash}))
    if fifth_origin == "direct_memory":
        run = runs["4"]
        run.analysis = None
        run.direct_resolution = SocDirectResolution(
            source_kind="memory",
            source_id="MEM-REVIEWED",
            source_version="1",
            source_hash="reviewed-hash",
            decision=Decision(verdict=Verdict.FALSE_POSITIVE, suggested_action="ignore", needs_review=False, reason="Previously reviewed exact behavior."),
            summary="Reviewed conclusion was reused.",
            memory_refs=["MEM-REVIEWED@v1"],
        )
        run.decision = run.direct_resolution.decision
    elif fifth_origin in {"memory_reference", "memory_retrieved_only"}:
        run = runs["4"]
        run.llm_analysis_request.context_catalog = [AnalysisContextCatalogItem(context_ref="M-000000000001", kind="confirmed_memory", label="Reviewed lesson", source_id="MEM-REVIEWED@v1", summary="Approved behavior context.")]
        if fifth_origin == "memory_reference":
            run.analysis.reasoning[0] = run.analysis.reasoning[0].model_copy(update={"basis": [AnalysisReasoningBasis.CURRENT_EVIDENCE, AnalysisReasoningBasis.CONFIRMED_MEMORY], "context_refs": ["M-000000000001"]})
    repo.corpus_experiments().prepare(experiment(), rows)
    if frozen_memory:
        reviewed_memory(repo)
    calls = []

    def analyze(payload, *, context):
        run = runs[payload["alert_id"]].model_copy(deep=True)
        calls.append((payload["alert_id"], context.idempotency_key))
        repo.save_run(run)
        return run

    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda member: payloads[member.alert_id], analysis_factory=lambda _: SimpleNamespace(analyze=analyze), profile_registry=SocMemoryProfileRegistry())
    service = SocCorpusExperimentService(repository=repo, execute=executor, configuration_provider=lambda options: {"model": "offline-fixture", "semantic_review": options.normalization_review_mode})
    learning = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning")), context=context())
    _finish(service, learning)
    return SimpleNamespace(repo=repo, service=service, executor=executor, learning=learning, rows=rows, runs=runs, calls=calls)


def _finish(service, round_):
    service.start(round_.round_id, context=context())
    while service.execute_one(round_.round_id):
        pass
    jobs = [item.job for item in service.store.list_round_items(round_.round_id).items]
    assert all(job.status.value == "completed" for job in jobs), [(job.alert_id, job.error_message) for job in jobs]
    return jobs


def _validation(fixture, *, alert_ids=None, frozen_memory=False):
    return fixture.service.create_round(
        CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", alert_ids=alert_ids or ["4"]), memory_mode="snapshot" if frozen_memory else "none"),
        context=context(),
    )


def test_validation_fifth_distinct_alert_joins_four_learning_occurrences_and_freezes_memory(tmp_path):
    fixture = _fixture(tmp_path, frozen_memory=True)
    before = fixture.service.store.list_round_items(fixture.learning.round_id).items
    assert [item.job.result_payload["summary"]["pattern_support_count"] for item in before] == [1, 2, 3, 4]
    assert not any(item.job.result_payload["candidate_id"] for item in before)
    validation = _validation(fixture, frozen_memory=True)
    assert len(validation.memory_snapshot) == 1
    frozen = validation.memory_snapshot
    assert validation.pattern_accumulation_scope == fixture.learning.pattern_accumulation_scope
    assert validation.pattern_accumulation_scope.accumulation_mode == "continuous"
    assert validation.pattern_accumulation_scope.configuration_hash is None
    assert validation.pattern_accumulation_scope.event_start == fixture.rows[0].event_time
    assert validation.pattern_accumulation_scope.event_end > fixture.rows[-1].event_time

    job = _finish(fixture.service, validation)[0]

    assert job.result_payload["summary"]["pattern_support_count"] == 5
    candidate = fixture.repo.get_memory_candidate(job.result_payload["candidate_id"])
    assert candidate is not None and candidate.status.value == "pending_review", job.result_payload
    assert candidate.source.metadata["experiment_id"] == "EXP-test"
    assert fixture.service.store.get_round(validation.round_id).memory_snapshot == frozen
    assert fixture.service.snapshot_problem(fixture.service.store.get_round(validation.round_id)) is None
    observations = [item for item in fixture.repo.list_memory_pattern_observations(limit=100) if item.source.alert_id in {str(i) for i in range(5)}]
    assert len(observations) == 5
    assert len({item.signature.value for item in observations}) == 1
    assert len({item.aggregation_key for item in observations}) == 1


def test_same_coarse_group_with_different_true_behavior_does_not_reach_threshold(tmp_path):
    fixture = _fixture(tmp_path, fifth_process="powershell.exe")
    validation = _validation(fixture, alert_ids=["4", "5"])
    jobs = _finish(fixture.service, validation)
    assert jobs[0].result_payload["summary"]["pattern_support_count"] == 1
    assert jobs[0].result_payload["candidate_id"] is None
    assert jobs[1].result_payload["summary"]["pattern_support_count"] == 5
    assert jobs[1].result_payload["candidate_id"]
    observations = fixture.repo.list_memory_pattern_observations(limit=100)
    assert len({item.signature.value for item in observations}) == 2


def test_changed_validation_options_do_not_split_the_same_behavior_accumulation(tmp_path):
    fixture = _fixture(tmp_path)
    validation = fixture.service.create_round(
        CorpusRoundCreateCommand(
            experiment_id="EXP-test",
            selection=CorpusRoundSelection(batch="validation", alert_ids=["4"]),
            memory_mode="none",
            purpose="full_flow",
            options={"normalization_review_mode": "apply", "tenant_policy_enabled": True},
        ),
        context=context(),
    )
    assert validation.config_hash != fixture.learning.config_hash
    assert validation.pattern_accumulation_scope == fixture.learning.pattern_accumulation_scope
    job = _finish(fixture.service, validation)[0]
    assert job.result_payload["summary"]["pattern_support_count"] == 5
    assert job.result_payload["candidate_id"]


@pytest.mark.parametrize("origin", ["direct_memory", "memory_reference", "memory_retrieved_only"])
def test_memory_dependent_result_counts_behavior_without_self_confirming_lesson(tmp_path, origin):
    fixture = _fixture(tmp_path, fifth_origin=origin)
    job = _finish(fixture.service, _validation(fixture))[0]
    assert job.result_payload["summary"]["pattern_support_count"] == 5
    assert job.result_payload["candidate_id"] is None
    assert "insufficient_conclusive_support" in job.result_payload["summary"]["pattern_reason_codes"]
    observation = fixture.repo.get_memory_pattern_observation(job.result_payload["observation_id"])
    assert observation.lesson is None
    assert observation.metadata["conclusion_origin"] == ("memory_reuse" if origin == "direct_memory" else "memory_reference")
    assert fixture.repo.get_run(job.run_id).analysis == fixture.runs["4"].analysis


def test_reanalysis_of_same_alert_does_not_increment_recurrence_support(tmp_path):
    fixture = _fixture(tmp_path)
    validation = _validation(fixture)
    job = _finish(fixture.service, validation)[0]
    original = fixture.repo.get_run(job.run_id)
    fixture.runs["4"] = original.model_copy(update={"run_id": "RUN-SEMANTIC-RETRY", "replay_of_run_id": original.run_id, "started_at": original.started_at + timedelta(seconds=1)}, deep=True)
    retry = fixture.executor(validation, fixture.rows[4], context().model_copy(update={"idempotency_key": "explicit-reanalysis-attempt"}))
    assert retry.run_id != original.run_id
    assert retry.summary["pattern_support_count"] == 5
    previous_candidate = fixture.repo.get_memory_candidate(job.result_payload["candidate_id"])
    assert previous_candidate is not None and previous_candidate.status.value != "confirmed"
    if retry.candidate_id:
        assert fixture.repo.get_memory_candidate(retry.candidate_id).status.value == "pending_review"
    assert len(fixture.repo.list_memory_pattern_observations(limit=100)) == 5
    assert fixture.repo.get_run(original.run_id) == original
