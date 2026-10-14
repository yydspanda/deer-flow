"""A batch continue must not silently widen a fixed report-based retest."""

from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, repository
from test_soc_corpus_experiments import context, prepare, service

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import CorpusQuickCommand, CorpusRetestProvenance, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation
from soc_agent.utils.hashing import stable_hash


def _setup(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id=experiment().plan_id))
    return svc, quick, calls


def _parent(svc, batch="learning", alert_ids=None, parent_round_id=None):
    round_ = svc.create_round(
        CorpusRoundCreateCommand(
            experiment_id="EXP-test",
            selection=CorpusRoundSelection(batch=batch, alert_ids=alert_ids or []),
            purpose="full_flow",
            options=SocAnalysisExecutionOptions(normalization_review_mode="apply", tenant_policy_enabled=True, tenant_policy_advisor_enabled=True, tenant_policy_signal_providers_enabled=True),
            memory_mode="none",
            execution_limit=100,
            parent_round_id=parent_round_id,
        ),
        context=context(),
    )
    svc.pause(round_.round_id, context=context())
    return svc.store.get_round(round_.round_id)


def _child(svc, parent, *, retest=True):
    selection = CorpusRoundSelection(batch=parent.selection.batch, alert_ids=["0", "1"] if parent.selection.batch == "learning" else ["5"])
    provenance = CorpusRetestProvenance(
        parent_round_id=parent.round_id,
        report_sha256="f" * 64,
        selection_hash=stable_hash(selection.model_dump(mode="json")),
        source_identity=experiment().source_identity,
    )
    return svc.create_round(
        CorpusRoundCreateCommand(
            experiment_id="EXP-test",
            selection=selection,
            options=SocAnalysisExecutionOptions(normalization_review_mode="apply"),
            memory_mode="none",
            execution_limit=100,
            parent_round_id=parent.round_id,
            retest_provenance=provenance if retest else None,
        ),
        context=context(),
    )


def _snapshot(svc):
    return {round_.round_id: {"round": round_.model_dump(mode="json"), "jobs": svc.store.list_round_items(round_.round_id).model_dump(mode="json")} for round_ in svc.store.list_rounds(experiment_id="EXP-test")}


@pytest.mark.parametrize("batch", ["learning", "validation"])
@pytest.mark.parametrize("child_completed", [False, True], ids=["paused-retest", "completed-retest"])
def test_quick_continue_rejects_old_pending_work_outside_fixed_retest(tmp_path, batch, child_completed):
    svc, quick, calls = _setup(tmp_path)
    parent = _parent(svc, batch)
    child = _child(svc, parent)
    svc.start(child.round_id, context=context())
    if child_completed:
        while svc.execute_one(child.round_id):
            pass
        assert svc.store.get_round(child.round_id).state == "completed"
    quick.command(CorpusQuickCommand(batch=batch, action="pause", options=child.options), context=context())
    before, previous_calls = _snapshot(svc), list(calls)

    with pytest.raises(ValueError, match="定向复测"):
        quick.command(CorpusQuickCommand(batch=batch, action="start", options=child.options), context=context())

    assert _snapshot(svc) == before
    assert calls == previous_calls
    assert svc.store.latest_run_options("EXP-test", batch) == child.options


def test_quick_continue_rejects_creating_unsubmitted_work_outside_fixed_retest(tmp_path):
    svc, quick, calls = _setup(tmp_path)
    parent = _parent(svc, alert_ids=["0", "1"])
    child = _child(svc, parent)
    svc.pause(child.round_id, context=context())
    before = _snapshot(svc)

    with pytest.raises(ValueError, match="定向复测"):
        quick.command(CorpusQuickCommand(batch="learning", action="start", options=child.options), context=context())

    assert _snapshot(svc) == before
    assert calls == []


def test_quick_continue_preserves_completed_report_and_paused_full_flow_sibling(tmp_path):
    svc, quick, calls = _setup(tmp_path)
    report = svc.create_round(
        CorpusRoundCreateCommand(
            experiment_id="EXP-test",
            selection=CorpusRoundSelection(batch="learning"),
            options=SocAnalysisExecutionOptions(normalization_review_mode="apply"),
            memory_mode="none",
            execution_limit=100,
        ),
        context=context(),
    )
    svc.start(report.round_id, context=context())
    while svc.execute_one(report.round_id):
        pass
    assert svc.store.get_round(report.round_id).state == "completed"
    sibling = _parent(svc, parent_round_id=report.round_id)
    retest = _child(svc, svc.store.get_round(report.round_id))
    svc.start(retest.round_id, context=context())
    quick.command(CorpusQuickCommand(batch="learning", action="pause", options=retest.options), context=context())
    before, previous_calls = _snapshot(svc), list(calls)
    assert retest.parent_round_id == report.round_id
    assert sibling.parent_round_id == report.round_id
    assert sibling.options.tenant_policy_enabled
    assert not retest.options.tenant_policy_enabled
    assert svc.store.get_round(sibling.round_id).state == "paused"

    with pytest.raises(ValueError, match="定向复测"):
        quick.command(CorpusQuickCommand(batch="learning", action="start", options=retest.options), context=context())

    assert _snapshot(svc) == before
    assert calls == previous_calls
    assert svc.store.latest_run_options("EXP-test", "learning") == retest.options


def test_explicit_round_resume_only_executes_fixed_retest(tmp_path):
    svc, quick, calls = _setup(tmp_path)
    parent = _parent(svc)
    child = _child(svc, parent)
    svc.pause(child.round_id, context=context())
    before_parent = _snapshot(svc)[parent.round_id]

    svc.start(child.round_id, context=context())
    while svc.execute_one(child.round_id):
        pass

    assert [call[1] for call in calls] == child.selection.alert_ids
    assert _snapshot(svc)[parent.round_id] == before_parent
    assert not svc.execute_one(parent.round_id)
    assert svc.store.get_round(child.round_id).options == child.options
    assert quick.snapshot("learning")["completed"] == 2


def test_quick_pause_still_pauses_all_running_rounds_with_fixed_retest(tmp_path):
    svc, quick, calls = _setup(tmp_path)
    parent = _parent(svc)
    child = _child(svc, parent)
    svc.start(parent.round_id, context=context())
    svc.start(child.round_id, context=context())
    before = _snapshot(svc)

    quick.command(CorpusQuickCommand(batch="learning", action="pause", options=child.options), context=context())

    for round_ in svc.store.list_rounds():
        assert round_.state == "paused"
        assert round_.options.model_dump(mode="json") == before[round_.round_id]["round"]["options"]
        assert svc.store.list_round_items(round_.round_id).model_dump(mode="json") == before[round_.round_id]["jobs"]
        assert not svc.execute_one(round_.round_id)
    assert calls == []


def test_quick_continue_can_resume_only_remaining_fixed_retest_jobs(tmp_path):
    svc, quick, calls = _setup(tmp_path)
    parent = _parent(svc)
    svc.start(parent.round_id, context=context())
    while svc.execute_one(parent.round_id):
        pass
    child = _child(svc, svc.store.get_round(parent.round_id))
    svc.pause(child.round_id, context=context())
    before_parent = _snapshot(svc)[parent.round_id]
    previous_calls = len(calls)

    quick.command(CorpusQuickCommand(batch="learning", action="start", options=child.options), context=context())
    while svc.execute_one(child.round_id):
        pass

    assert [call[1] for call in calls[previous_calls:]] == child.selection.alert_ids
    assert _snapshot(svc)[parent.round_id] == before_parent
    assert svc.store.get_round(child.round_id).options == child.options


def test_ordinary_multiple_round_continue_preserves_existing_batch_behavior(tmp_path):
    svc, quick, calls = _setup(tmp_path)
    parent = _parent(svc)
    child = _child(svc, parent, retest=False)
    svc.pause(child.round_id, context=context())

    quick.command(CorpusQuickCommand(batch="learning", action="start", options=child.options), context=context())

    resumed_parent, resumed_child = svc.store.get_round(parent.round_id), svc.store.get_round(child.round_id)
    assert resumed_parent.state == resumed_child.state == "running"
    assert resumed_parent.options == parent.options
    assert resumed_child.options == child.options
    assert set(resumed_parent.dispatch_alert_ids).isdisjoint(child.selection.alert_ids)
    assert resumed_child.dispatch_alert_ids == child.selection.alert_ids
    assert len(resumed_parent.dispatch_alert_ids) + len(resumed_child.dispatch_alert_ids) == 10
    assert calls == []


def test_later_single_rerun_cannot_hide_completed_retest_scope(tmp_path):
    svc, quick, calls = _setup(tmp_path)
    parent = _parent(svc)
    child = _child(svc, parent)
    svc.start(child.round_id, context=context())
    while svc.execute_one(child.round_id):
        pass
    quick.command(
        CorpusQuickCommand(batch="learning", action="rerun", alert_id="0", options=child.options),
        context=context().model_copy(update={"idempotency_key": "single-rerun-after-retest"}),
    )
    latest_id, _ = svc.store.latest_round_identity("EXP-test", "learning")
    assert not svc.store.get_round(latest_id).retest_provenance
    before, previous_calls = _snapshot(svc), list(calls)

    with pytest.raises(ValueError, match="定向复测"):
        quick.command(CorpusQuickCommand(batch="learning", action="start", options=child.options), context=context())

    assert _snapshot(svc) == before
    assert calls == previous_calls
    assert svc.store.get_round(parent.round_id).state == "paused"


def test_whole_batch_restart_retires_retest_fence_for_normal_continue(tmp_path):
    svc, quick, calls = _setup(tmp_path)
    parent = _parent(svc)
    child = _child(svc, parent)
    svc.pause(child.round_id, context=context())
    token = quick.snapshot("learning")["restart_token"]
    quick.command(
        CorpusQuickCommand(batch="learning", action="restart_learning", restart_token=token, options=child.options),
        context=context().model_copy(update={"idempotency_key": f"restart-learning-{token}"}),
    )
    replacement_id, _ = svc.store.latest_round_identity("EXP-test", "learning")
    assert svc.store.get_round(child.round_id).superseded_by_round_id == replacement_id
    quick.command(CorpusQuickCommand(batch="learning", action="pause", options=child.options), context=context())
    before = _snapshot(svc)

    quick.command(CorpusQuickCommand(batch="learning", action="start", options=child.options), context=context())

    assert svc.store.get_round(replacement_id).state == "running"
    assert _snapshot(svc)[child.round_id] == before[child.round_id]
    assert _snapshot(svc)[parent.round_id] == before[parent.round_id]
    assert calls == []
