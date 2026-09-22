"""Read-only batch browsing; no model or Memory mutations."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, text
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
    get_corpus_workbench_state(service, request=None, batch="validation", validation_tier="supplementary")
    get_corpus_workbench_groups(service, batch="validation", validation_tier="main")
    assert calls[0]["batch"] == calls[1]["batch"] == "validation"
    assert calls[0]["validation_tier"] == "supplementary"
    assert calls[1]["validation_tier"] == "main"


def test_durable_prechecks_filter_first_runs_and_reruns_before_a_new_run_exists(workbench):
    from test_soc_corpus_experiments import context, service

    from soc_agent.contracts import ProcessingJobStatus
    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
    from soc_agent.core import SocAnalysisService

    # A saved prior result must disappear from success as soon as its rerun is claimed.
    payload = json.loads((Path(__file__).resolve().parents[1] / "samples/alerts/approved_scanner.json").read_text(encoding="utf-8"))
    prior = SocAnalysisService().analyze(payload)
    prior.alert_id = "1"
    prior.input_hash = workbench._cases["1"].payload_hash
    prior.llm_analysis_request = prior.llm_analysis_request.model_copy(update={"environment": "dev-corpus-eval"})
    workbench._repository.save_run(prior)
    query = dict(batch="learning", unprocessed_only=False, include_rehearsal=False, include_group_catalog=False)
    before = workbench.get_state(run_status="success", **query)
    assert [a.alert_id for a in before.alerts] == ["1"]
    assert before.alerts[0].operator_outcome is not None

    batches = service(workbench._repository, [])
    batches.prepare(workbench.batch_plan, experiment_id="EXP-current", name="Current corpus", context=context())
    round_ = batches.create_round(CorpusRoundCreateCommand(experiment_id="EXP-current", selection=CorpusRoundSelection(batch="learning")), context=context())
    jobs = {item.alert_id: item.job for item in batches.store.list_round_items(round_.round_id).items}
    assert workbench.get_activity().active_count == 0  # Queued is not executing.
    for alert_id in ("0", "1"):
        claimed = batches.jobs.claim_next(queue_name=jobs[alert_id].queue_name, job_ids=[jobs[alert_id].job_id], worker_id="fixture", lease_seconds=120)
        assert claimed is not None
        if alert_id == "1":
            batches.jobs.transition(claimed.job_id, worker_id="fixture", expected_status=ProcessingJobStatus.CLAIMED, target_status=ProcessingJobStatus.PRECHECKING, event_type="fixture_precheck")

    assert workbench._active_executions == {}  # Includes durable/recovered work with no local claim.
    activity = workbench.get_activity()
    assert activity.active_count == 2
    assert {item.alert_id for item in activity.executions} == {"0", "1"}
    assert {item.execution_id for item in activity.executions} == {jobs["0"].job_id, jobs["1"].job_id}
    assert all(item.actor_id == "operator" and item.actor_surface == "daemon" and item.elapsed_ms >= 0 for item in activity.executions)
    running = workbench.get_state(run_status="running", limit=1, offset=1, **query)
    assert running.alert_page.total == 2
    assert [a.alert_id for a in running.alerts] == ["1"]
    assert running.alerts[0].run_id == prior.run_id
    assert running.alerts[0].workflow_state == "running"
    assert running.alerts[0].operator_outcome is None
    assert running.alerts[0].active_execution is not None
    first_run = workbench._get_alert_view("0")
    assert first_run.workflow_state == "running" and first_run.run_id is None
    assert first_run.operator_outcome is None and not first_run.can_process
    assert workbench.get_state(run_status="success", **query).alert_page.total == 0
    assert workbench.get_state(run_status="not_run", **query).alert_page.total == workbench.batch_plan.counts["learning"] - 2


def test_activity_preserves_legacy_browsing_until_batch_schema_is_upgraded(workbench):
    from test_soc_corpus_experiments import context, service

    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
    from soc_agent.db import create_soc_tables
    from soc_agent.db.corpus_experiments import CorpusExperimentSchemaNotReady

    repo = workbench._repository
    with repo._session_factory() as session:
        engine = session.get_bind()
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE soc_corpus_rounds"))
    assert workbench.get_activity().active_count == 0
    assert len(workbench.get_state(unprocessed_only=False, include_rehearsal=False).alerts) == 20
    assert "soc_corpus_rounds" not in inspect(engine).get_table_names()
    with pytest.raises(CorpusExperimentSchemaNotReady, match="升级数据库"):
        repo.corpus_experiments().require_schema()

    # A startup/operator migration is observed on refresh, without caching the failure.
    create_soc_tables(engine)
    batches = service(repo, [])
    batches.prepare(workbench.batch_plan, experiment_id="EXP-upgraded", name="Upgraded corpus", context=context())
    round_ = batches.create_round(CorpusRoundCreateCommand(experiment_id="EXP-upgraded", selection=CorpusRoundSelection(batch="learning")), context=context())
    job = batches.store.list_round_items(round_.round_id).items[0].job
    assert batches.jobs.claim_next(queue_name=job.queue_name, job_ids=[job.job_id], worker_id="fixture", lease_seconds=120) is not None
    assert [item.execution_id for item in workbench.get_activity().executions] == [job.job_id]


def test_failed_jobs_before_runtime_are_filtered_and_cleared_by_a_new_attempt(workbench):
    from test_soc_corpus_experiments import context

    from soc_agent.contracts import AnalysisRunStatus, RuntimeFailure
    from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusRoundCreateCommand, CorpusRoundSelection
    from soc_agent.core import SocAnalysisService
    from soc_agent.demo.corpus_experiments import SocCorpusExperimentService

    repo = workbench._repository
    payload = json.loads((Path(__file__).resolve().parents[1] / "samples/alerts/approved_scanner.json").read_text(encoding="utf-8"))
    prior = SocAnalysisService().analyze(payload)
    prior.alert_id = "1"
    prior.input_hash = workbench._cases["1"].payload_hash
    prior.llm_analysis_request = prior.llm_analysis_request.model_copy(update={"environment": "dev-corpus-eval"})
    repo.save_run(prior)
    query = dict(batch="learning", unprocessed_only=False, include_rehearsal=False, include_group_catalog=False)
    assert workbench.get_state(run_status="success", **query).alert_page.total == 1

    def fail_before_runtime(*_):
        raise ValueError("fixture payload cannot be loaded")

    batches = SocCorpusExperimentService(repository=repo, execute=fail_before_runtime, configuration_provider=lambda _: {})
    batches.prepare(workbench.batch_plan, experiment_id="EXP-current", name="Current corpus", context=context())
    failed_round = batches.create_round(CorpusRoundCreateCommand(experiment_id="EXP-current", selection=CorpusRoundSelection(batch="learning", alert_ids=["0", "1"])), context=context())
    batches.start(failed_round.round_id, context=context())
    assert batches.execute_one(failed_round.round_id)
    assert batches.execute_one(failed_round.round_id)
    assert batches.store.round_progress(failed_round.round_id).failed_count == 2
    assert all(item.job.run_id is None for item in batches.store.list_round_items(failed_round.round_id).items)
    failed = workbench.get_state(run_status="failed", limit=1, offset=1, **query)
    assert failed.alert_page.total == 2
    assert [item.alert_id for item in failed.alerts] == ["1"]
    assert failed.alerts[0].workflow_state == "failed"
    assert failed.alerts[0].failure_kind == "ValueError"
    assert failed.alerts[0].failure_message == "fixture payload cannot be loaded"
    assert failed.alerts[0].operator_outcome is None
    assert failed.alerts[0].run_id is None  # The prior successful Run remains history only.
    assert workbench._get_alert_view("0").workflow_state == "failed"
    trace = workbench.get_execution("1")
    assert trace.status == "failed" and trace.run_id is None
    assert workbench.get_state(run_status="success", **query).alert_page.total == 0
    assert workbench.get_state(run_status="not_run", **query).alert_page.total == workbench.batch_plan.counts["learning"] - 2

    recovered_run = prior.model_copy(update={"run_id": "RUN-recovered", "started_at": datetime.now(UTC)})

    def succeed(round_, member, request):
        active = workbench.get_state(run_status="running", **query)
        assert [item.alert_id for item in active.alerts] == ["1"]
        assert active.alerts[0].operator_outcome is None
        assert workbench.get_state(run_status="failed", **query).alert_page.total == 1
        repo.save_run(recovered_run)
        return CorpusExecutionOutcome(run_id=recovered_run.run_id)

    recovered = SocCorpusExperimentService(repository=repo, execute=succeed, configuration_provider=lambda _: {})
    rerun = recovered.create_round(CorpusRoundCreateCommand(experiment_id="EXP-current", selection=CorpusRoundSelection(batch="learning", alert_ids=["1"])), context=context())
    assert workbench.get_state(run_status="failed", **query).alert_page.total == 1  # Latest queued attempt supersedes the failed job.
    recovered.start(rerun.round_id, context=context())
    assert recovered.execute_one(rerun.round_id)
    assert [item.alert_id for item in workbench.get_state(run_status="failed", **query).alerts] == ["0"]
    success = workbench.get_state(run_status="success", **query)
    assert [item.alert_id for item in success.alerts] == ["1"]
    assert success.alerts[0].run_id == recovered_run.run_id
    assert success.alerts[0].failure_message is None
    assert len(recovered.store.alert_history("EXP-current", "1")) == 2
    assert repo.get_run(prior.run_id) is not None

    old_failed_run = prior.model_copy(
        update={
            "run_id": "RUN-old-failed",
            "alert_id": "0",
            "input_hash": workbench._cases["0"].payload_hash,
            "status": AnalysisRunStatus.FAILED,
            "failure": RuntimeFailure(step_name="analyze_llm", kind="analyzer_timeout", error_type="TimeoutError", message="old timeout"),
        }
    )
    repo.save_run(old_failed_run)
    retry = recovered.create_round(CorpusRoundCreateCommand(experiment_id="EXP-current", selection=CorpusRoundSelection(batch="learning", alert_ids=["0"])), context=context())
    retry_job = recovered.store.list_round_items(retry.round_id).items[0].job
    assert recovered.jobs.claim_next(queue_name=retry_job.queue_name, job_ids=[retry_job.job_id], worker_id="fixture", lease_seconds=120) is not None
    active = workbench.get_state(run_status="running", **query).alerts[0]
    assert active.alert_id == "0" and active.run_id == old_failed_run.run_id
    assert active.operator_outcome is None
    assert active.failure_kind is None and active.failure_message is None


def test_failed_job_after_runtime_does_not_show_a_successful_operator_outcome(workbench):
    from test_soc_corpus_experiments import context

    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
    from soc_agent.core import SocAnalysisService
    from soc_agent.demo.corpus_experiments import CorpusExecutionError, SocCorpusExperimentService

    repo = workbench._repository
    payload = json.loads((Path(__file__).resolve().parents[1] / "samples/alerts/approved_scanner.json").read_text(encoding="utf-8"))
    run = SocAnalysisService().analyze(payload)
    run.alert_id = "0"
    run.input_hash = workbench._cases["0"].payload_hash
    run.llm_analysis_request = run.llm_analysis_request.model_copy(update={"environment": "dev-corpus-eval"})

    def fail_after_runtime(*_):
        repo.save_run(run)
        raise CorpusExecutionError("fixture observation failed", run_id=run.run_id)

    batches = SocCorpusExperimentService(repository=repo, execute=fail_after_runtime, configuration_provider=lambda _: {})
    batches.prepare(workbench.batch_plan, experiment_id="EXP-current", name="Current corpus", context=context())
    round_ = batches.create_round(CorpusRoundCreateCommand(experiment_id="EXP-current", selection=CorpusRoundSelection(batch="learning", alert_ids=["0"])), context=context())
    batches.start(round_.round_id, context=context())
    assert batches.execute_one(round_.round_id)
    query = dict(batch="learning", unprocessed_only=False, include_rehearsal=False, include_group_catalog=False)
    assert workbench.get_state(run_status="success", **query).alert_page.total == 0
    failed = workbench.get_state(run_status="failed", **query)
    assert failed.alert_page.total == 1
    assert failed.alerts[0].run_id == run.run_id
    assert failed.alerts[0].operator_outcome is None
    assert failed.alerts[0].failure_message == "fixture observation failed"
    trace = workbench.get_execution("0")
    assert trace.status == "failed" and trace.run_id == run.run_id
    assert workbench._audit_run_for_case(workbench._cases["0"], run.run_id).status == run.status


def test_queued_validation_restart_hides_old_result_but_keeps_fixed_history(workbench):
    from test_soc_corpus_experiments import context, service

    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
    from soc_agent.core import SocAnalysisService

    member = next(m for m in workbench.batch_plan.members if m.batch == "validation")
    alert_id = member.alert_id
    payload = json.loads((Path(__file__).resolve().parents[1] / "samples/alerts/approved_scanner.json").read_text(encoding="utf-8"))
    prior = SocAnalysisService().analyze(payload)
    prior.alert_id = alert_id
    prior.input_hash = workbench._cases[alert_id].payload_hash
    prior.llm_analysis_request = prior.llm_analysis_request.model_copy(update={"environment": "dev-corpus-eval"})
    workbench._repository.save_run(prior)
    query = dict(batch="validation", unprocessed_only=False, include_rehearsal=False, include_group_catalog=False)
    assert workbench.get_state(run_status="success", **query).alert_page.total == 1

    # A previous semantic interpretation can give this alert a different readiness.
    # The new queued attempt resets both detail and SQL filtering to source facts.
    from dataclasses import replace

    revision, summary = workbench._list_queries.rows(workbench._list_catalog_id)[alert_id]
    workbench._list_queries.save(workbench._list_catalog_id, alert_id, revision, replace(summary, readiness="singleton_strong"))

    batches = service(workbench._repository, [])
    batches.prepare(workbench.batch_plan, experiment_id="EXP-current", name="Current corpus", context=context())
    batches.create_round(CorpusRoundCreateCommand(experiment_id="EXP-current", selection=CorpusRoundSelection(batch="validation", scope="all"), memory_mode="none"), context=context())
    assert workbench.get_state(run_status="success", **query).alert_page.total == 0
    pending = workbench.get_state(run_status="not_run", **query)
    assert pending.alert_page.total == workbench.batch_plan.counts["validation_main"] + workbench.batch_plan.counts["validation_supplementary"]
    row = next(a for a in pending.alerts if a.alert_id == alert_id)
    assert row.workflow_state == "ready" and row.run_id is None
    assert row.operator_outcome is None
    source_readiness = workbench._cases[alert_id].readiness
    assert row.readiness == source_readiness
    assert workbench.get_state(search=alert_id, readiness=source_readiness, **query).alert_page.total == 1
    assert workbench.get_state(search=alert_id, readiness="singleton_strong", **query).alert_page.total == 0
    assert workbench._get_alert_view(alert_id).run_id is None
    execution = workbench.get_execution(alert_id)
    assert execution.status == "not_started" and execution.run_id is None
    assert workbench._audit_run_for_case(workbench._cases[alert_id], prior.run_id).run_id == prior.run_id
    assert workbench._repository.get_run(prior.run_id) is not None
