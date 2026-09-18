"""Isolated two-batch closure with the real services and a deterministic analyzer.

No corpus files, existing DEV database, external models or ZEUS endpoints are used.
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context
from test_soc_memory_governance import confirm
from test_soc_pingan_memory_profile import _run as sample

from soc_agent.application import analysis as composition
from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import AlertInput, Verdict
from soc_agent.contracts.corpus_experiments import CorpusQuickCommand, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.core import SocMemoryService
from soc_agent.demo.corpus_experiment_reports import compare_reports, export_report
from soc_agent.demo.corpus_experiment_runtime import CorpusRuntimeExecutor
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation
from soc_agent.llm import SocLLMSettings
from soc_agent.pipeline.analyzer import StubLLMAnalyzer
from soc_agent.utils.hashing import stable_hash


@pytest.mark.parametrize("quick_entry", [False, True])
def test_learning_review_validation_and_fixed_round_comparison(tmp_path, monkeypatch, quick_entry):
    repo = repository(tmp_path)
    registry = build_soc_memory_profile_registry()
    template = sample(1).llm_analysis_request
    payloads, manifest = {}, []
    for row in members(6):
        # More than 30 days apart, but within this experiment's learning scope.
        row = row.model_copy(update={"event_time": row.event_time + timedelta(days=40 * row.sequence_number)})
        payload = AlertInput(
            tenant_id="pingan",
            alert_id=row.alert_id,
            source=template.source,
            detection=template.detection,
            classification=template.classification,
            entities=template.canonical_entities,
            event={"event_time": row.event_time},
        ).model_dump(mode="json")
        payloads[row.alert_id] = payload
        manifest.append(row.model_copy(update={"payload_hash": stable_hash(payload)}))
    repo.corpus_experiments().prepare(experiment(), manifest)
    calls = []

    class MockAnalyzer(StubLLMAnalyzer):
        def analyze(self, request):
            calls.append(request.alert_id)
            output = super().analyze(request)
            output.analysis = output.analysis.model_copy(update={"verdict": Verdict.TRUE_POSITIVE, "confidence": 0.9, "recommended_action": "transfer"})
            return output

    monkeypatch.setattr(composition, "build_configured_analysis_nodes", lambda **_: (MockAnalyzer(), None))
    monkeypatch.delenv("SOC_AUTOMATION_POLICY_PATH", raising=False)
    executor = CorpusRuntimeExecutor(
        repository=repo,
        load_payload=lambda member: payloads[member.alert_id],
        profile_registry=registry,
        analysis_factory=lambda round_: composition.build_soc_analysis_service(
            repo,
            settings=SocLLMSettings(),
            runtime_environment="dev-corpus-eval",
            execution_options=round_.options,
            memory_record_ids=frozenset(e.memory_id for e in round_.memory_snapshot),
            pattern_observation_enabled=False,
            execute_authorized_actions=False,
        ),
    )
    svc = SocCorpusExperimentService(repository=repo, execute=executor, configuration_provider=lambda _: {"mocked": True})

    def run_batch(batch, *, memory_mode="snapshot"):
        if quick_entry:
            CorpusQuickValidation(svc, SimpleNamespace(plan_id=experiment().plan_id)).command(CorpusQuickCommand(batch=batch), context=context())
            round_ = svc.store.list_rounds()[-1]
        else:
            round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch=batch), memory_mode=memory_mode), context=context())
            svc.start(round_.round_id, context=context())
        while svc.execute_one(round_.round_id):
            pass
        progress = svc.store.round_progress(round_.round_id)
        assert progress.completed_count == progress.selected_count, svc.store.list_round_items(round_.round_id)
        return svc.store.get_round(round_.round_id), svc.store.list_round_items(round_.round_id).items

    learn, learned = run_batch("learning")
    assert learn.memory_snapshot == []
    assert [i.job.result_payload["summary"]["pattern_support_count"] for i in learned] == [1, 2, 3, 4, 5]
    candidate_id = learned[-1].job.result_payload["candidate_id"]
    assert candidate_id, learned[-1].job.result_payload
    candidate = repo.get_memory_candidate(candidate_id)
    assert candidate.status.value == "pending_review"
    page = svc.store.list_learning_candidates("EXP-test")
    assert page.total == 1 and page.items[0].candidate_id == candidate_id
    assert svc.store.list_learning_candidates("EXP-other").total == 0
    assert not svc.store.list_learning_candidates("EXP-test", offset=1).items
    assert not repo.list_memory_records()
    # This is the test's explicit human-review simulation, not automatic approval.
    memory_service = SocMemoryService(candidate_repository=repo, record_repository=repo, mutation_audit_repository=repo, profile_registry=registry)
    record = confirm(memory_service, candidate, Verdict.FALSE_POSITIVE)
    assert svc.store.list_learning_candidates("EXP-test").total == 0
    assert svc.store.list_learning_candidates("EXP-test", review_stage="confirmed").items[0].candidate_id == candidate_id
    assert record.source_candidate_id == candidate_id
    before_observations = repo.list_memory_pattern_observations(limit=100)
    if quick_entry:
        validation, after = run_batch("validation")
        quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id=experiment().plan_id))
        assert quick.snapshot("learning")["completed"] == 5
        assert quick.snapshot("validation")["completed"] == 1
        assert after[0].job.result_payload["summary"]["processing_path"] == "memory"
        assert calls == ["0", "1", "2", "3", "4"]
        return
    baseline, before = run_batch("validation", memory_mode="none")
    validation, after = run_batch("validation")
    assert [e.memory_id for e in validation.memory_snapshot] == [record.memory_id]
    assert calls == ["0", "1", "2", "3", "4", "5"]
    assert after[0].job.result_payload["summary"]["processing_path"] == "memory"
    assert after[0].job.result_payload["summary"]["recommended_handling"] == "ignore"
    uses = repo.list_memory_uses(run_id=after[0].job.run_id)
    assert len(uses) == 1 and uses[0].memory_id == record.memory_id
    assert uses[0].directive_applied
    assert repo.list_memory_pattern_observations(limit=100) == before_observations
    assert len(repo.list_memory_candidates()) == 1
    assert repo.get_memory_record(record.memory_id).version == record.version
    # Resume must not re-run completed members or fabricate another confirmation.
    svc.start(validation.round_id, context=context())
    assert not svc.execute_one(validation.round_id)
    assert len(calls) == 6

    def report(round_, items):
        return {
            "round": round_.model_dump(mode="json"),
            "source_identity": experiment().source_identity,
            "rows": [{"alert_id": i.alert_id, "validation_tier": "main", "status": i.job.status.value, "run_id": i.job.run_id, **i.job.result_payload} for i in items],
        }

    export_report(report(baseline, before), tmp_path / "before")
    export_report(report(validation, after), tmp_path / "after")
    exported_before = json.loads((tmp_path / "before/report.json").read_text(encoding="utf-8"))
    exported_after = json.loads((tmp_path / "after/report.json").read_text(encoding="utf-8"))
    comparison = compare_reports(exported_before, exported_after)
    assert comparison["paired_alerts"] == 1
    assert comparison["changed_handling"] == 1
    assert comparison["after_metrics"]["reuse"]["memory_direct_reuse"]["numerator"] == 1
    assert comparison["after_metrics"]["reuse"]["historical_handling_agreement"]["rate"] is None
    assert not comparison["causal_attribution_allowed"]
