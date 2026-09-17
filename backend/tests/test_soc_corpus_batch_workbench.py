"""Read-only batch browsing; no model or Memory mutations."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_soc_corpus_workbench import _repository

from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchService, _CorpusCase
from soc_agent.llm import SocAnalyzerMode, SocLLMSettings


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    source = tmp_path / "fixture.pkl"
    source.write_bytes(b"synthetic inventory, no pickle loading")
    cases = {}
    for i in range(24):
        at = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(hours=i)
        group = "large" if i < 20 else "small" if i < 23 else "singleton"
        size = 20 if i < 20 else 3 if i < 23 else 1
        cases[str(i)] = _CorpusCase(
            alert_id=str(i),
            source_index=i,
            sequence_number=i + 1,
            payload=None,
            payload_hash=f"{i:064x}",
            observed_at=at.isoformat(),
            observed_at_value=at,
            topic="fixture",
            source_type="ndr",
            source_system="test",
            product="test",
            detection_key="rule",
            rule_code="RULE-1",
            rule_name="Example rule",
            category=None,
            severity=None,
            endpoint=None,
            host_name=None,
            process_names=(),
            behavior_fingerprint="fp",
            behavior_components=("protocol:tcp",),
            behavior_strength="strong",
            decision_eligible=True,
            group_id=group,
            window_id=group,
            window_start=at.isoformat(),
            window_end=at.isoformat(),
            group_alert_count=size,
            window_alert_count=size,
            readiness="candidate_window",
        )
    monkeypatch.setattr("soc_agent.demo.corpus_workbench._load_cases", lambda *a, **kw: cases)
    return SocCorpusWorkbenchService(
        repository=_repository(tmp_path),
        analysis_service=SimpleNamespace(),
        pattern_service=SimpleNamespace(),
        source_path=source,
        settings=SocLLMSettings(mode=SocAnalyzerMode.STUB),
        database_file="fixture.sqlite",
    )


def test_batch_pages_match_frozen_plan_with_no_focus_escape(workbench):
    plan = workbench._batch_plan
    views = {}
    for batch, tier, key in [("learning", None, "learning"), ("validation", "main", "validation_main"), ("validation", "supplementary", "validation_supplementary")]:
        state = workbench.get_state(batch=batch, validation_tier=tier, unprocessed_only=False, include_group_catalog=False, include_rehearsal=False, limit=500)
        views[key] = state
        assert state.batch_selection.plan_id == plan.plan_id
        assert state.batch_selection.execution_enabled is False
        assert state.alert_page.total == plan.counts[key]
        assert state.readiness.total_alert_count == plan.counts[key]
        assert state.batch_selection.selected_count == plan.counts[key]
        assert state.leadership_demo is None
        assert state.rehearsal_alerts == []
        assert all(a.batch == batch and a.validation_tier == tier and not a.can_process for a in state.alerts)
    assert not {a.alert_id for a in views["learning"].alerts} & {a.alert_id for a in views["validation_main"].alerts}
    main_id = views["validation_main"].alerts[0].alert_id
    assert not workbench.get_state(batch="learning", focus_alert_id=main_id, search=main_id, unprocessed_only=False).alerts
    assert sum(s.alert_page.total for s in views.values()) == len(workbench._cases)


def test_batch_group_directory_counts_and_alert_search_are_scoped(workbench):
    expected = {m.alert_id for m in workbench._batch_plan.members if m.batch == "learning"}
    page = workbench.get_groups(batch="learning", limit=100)
    assert sum(g.alert_count for g in page.groups) == len(expected)
    assert all(5 <= g.alert_count <= 10 for g in page.groups)
    group = page.groups[0]
    state = workbench.get_state(batch="learning", group_id=group.group_id, include_group_catalog=False, unprocessed_only=False)
    assert state.groups[0].alert_count == state.alert_page.total == group.alert_count
    assert all(a.batch_group_alert_count == group.alert_count for a in state.alerts)
    outside = next(m for m in workbench._batch_plan.members if m.validation_tier == "supplementary")
    assert workbench.get_groups(batch="learning", search=outside.alert_id).total == 0
    assert workbench.get_groups(batch="validation", validation_tier="supplementary", search=outside.alert_id).total == 1
    with pytest.raises(ValueError, match="validation"):
        workbench.get_state(batch="learning", validation_tier="main")


def test_experiment_payload_loader_cannot_substitute_member_identity(workbench):
    from soc_agent.contracts.corpus_experiments import CorpusExperimentMember

    member = workbench.batch_plan.members[0]
    fixed = CorpusExperimentMember(alert_id=member.alert_id, payload_hash="f" * 64, source_index=member.source_index, group_id=member.group_id, sequence_number=0, position_in_group=0, batch="learning", reason="test")
    with pytest.raises(ValueError, match="identity"):
        workbench.load_experiment_payload(fixed)


def test_fixed_audit_run_never_falls_back_to_latest(workbench, monkeypatch):
    from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchError

    saved = SimpleNamespace(run_id="RUN-fixed", alert_id="0")
    monkeypatch.setattr(workbench._repository, "get_run", lambda run_id: saved if run_id == "RUN-fixed" else None)
    monkeypatch.setattr("soc_agent.demo.corpus_workbench._matches_corpus_run", lambda run, case: case.alert_id == "0")
    monkeypatch.setattr(workbench, "_run_for_case", lambda case: pytest.fail("must not select the latest run"))
    assert workbench._audit_run_for_case(workbench._cases["0"], "RUN-fixed") is saved
    with pytest.raises(SocCorpusWorkbenchError, match="does not belong"):
        workbench._audit_run_for_case(workbench._cases["1"], "RUN-fixed")
    with pytest.raises(SocCorpusWorkbenchError, match="does not belong"):
        workbench._audit_run_for_case(workbench._cases["0"], "RUN-missing")


def test_preparing_batches_cannot_mix_with_untracked_interactive_runs(workbench):
    from test_soc_corpus_experiments import context

    from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchError

    claim = workbench._reserve_execution("0", context=context())
    try:
        with pytest.raises(SocCorpusWorkbenchError, match="仍有"):
            with workbench.experiment_preparation_guard():
                pytest.fail("preparation must wait for the interactive run")
    finally:
        workbench._release_execution(claim)
    with workbench.experiment_preparation_guard():
        from test_soc_corpus_experiments import prepare

        prepare(workbench._repository)
    with pytest.raises(SocCorpusWorkbenchError, match="批次轮次"):
        workbench._reserve_execution("0", context=context())


def test_batch_api_forwards_selection():
    from app.gateway.routers.soc_corpus_workbench import get_corpus_workbench_groups, get_corpus_workbench_state

    calls = []
    service = SimpleNamespace(get_state=lambda **kw: calls.append(kw), get_groups=lambda **kw: calls.append(kw))
    get_corpus_workbench_state(service, batch="validation", validation_tier="supplementary")
    get_corpus_workbench_groups(service, batch="validation", validation_tier="main")
    assert calls[0]["batch"] == calls[1]["batch"] == "validation"
    assert calls[0]["validation_tier"] == "supplementary"
    assert calls[1]["validation_tier"] == "main"
