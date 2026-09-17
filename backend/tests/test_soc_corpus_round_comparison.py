from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_api import client
from test_soc_corpus_experiment_repository import repository
from test_soc_corpus_experiments import context, prepare, service

from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiments import CorpusExecutionError
from soc_agent.demo.corpus_round_comparison import comparison_side, job_result_row


def command(**changes):
    return CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", alert_ids=["0"]), **changes)


def test_retest_preserves_failed_baseline_after_parent_is_retried(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    original = svc._execute

    def fail(*_):
        raise CorpusExecutionError("simulated failure", retryable=True)

    svc._execute = fail
    parent = svc.create_round(command(), context=context())
    svc.start(parent.round_id, context=context())
    svc.execute_one(parent.round_id)
    child = svc.create_round(command(parent_round_id=parent.round_id), context=context())
    baseline = svc.store.list_round_items(child.round_id).items[0].job.metadata["comparison_baseline"]
    assert baseline["result"]["status"] == "failed"
    assert baseline["result"]["run_id"] is None
    svc._execute = original
    svc.retry_failed(parent.round_id, context=context())
    svc.start(parent.round_id, context=context())
    svc.execute_one(parent.round_id)
    assert svc.store.round_progress(parent.round_id).completed_count == 1
    assert svc.store.list_round_items(child.round_id).items[0].job.metadata["comparison_baseline"] == baseline
    assert svc.store.round_progress(child.round_id).counts == {"queued": 1}


def test_comparison_captures_only_minimal_result_and_never_raw_or_labels():
    row = {
        "job_id": "J1",
        "status": "completed",
        "run_id": "RUN1",
        "attempt_count": 1,
        "label": {"expected_handling": "ignore"},
        "raw_alert": "DO_NOT_COPY",
        "summary": {
            "recommended_handling": "transfer",
            "total_duration_ms": 1200,
            "measurements": {"total_tokens": None},
            "raw_log": "DO_NOT_COPY",
            "memory_uses": [{"memory_id": "M1", "memory_version": 3, "directive_applied": False, "private_content": "DO_NOT_COPY", "effect": "context_only"}],
        },
    }
    view = comparison_side(row)
    assert view["summary"]["memory_uses"] == [{"memory_id": "M1", "memory_version": 3, "directive_applied": False, "effect": "context_only"}]
    assert "DO_NOT_COPY" not in str(view)
    assert "label" not in view
    assert view["summary"]["measurements"]["total_tokens"] is None


def test_parent_must_be_quiescent_and_in_same_batch(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    svc = service(repo, [])
    parent = svc.create_round(command(), context=context())
    svc.start(parent.round_id, context=context())
    with pytest.raises(ValueError, match="pause"):
        svc.create_round(command(parent_round_id=parent.round_id), context=context())
    svc.pause(parent.round_id, context=context())
    with pytest.raises(ValueError, match="batch"):
        svc.create_round(command(parent_round_id=parent.round_id).model_copy(update={"selection": CorpusRoundSelection(batch="validation"), "memory_mode": "none"}), context=context())


def test_comparison_api_is_bounded_read_only_and_uses_captured_parent(tmp_path):
    http, application, calls = client(tmp_path)
    application.workbench = SimpleNamespace(batch_plan_id="a" * 64)
    svc = application.service
    parent = svc.create_round(command(), context=context())
    svc.start(parent.round_id, context=context())
    svc.execute_one(parent.round_id)
    child_command = command(parent_round_id=parent.round_id).model_copy(update={"selection": CorpusRoundSelection(batch="learning", alert_ids=["0", "1"])})
    child = svc.create_round(child_command, context=context())
    svc._execute = lambda round_, member, _: CorpusExecutionOutcome(run_id="NEW-" + member.alert_id, summary={"recommended_handling": "ignore", "effective_verdict": "false_positive"})
    svc.start(child.round_id, context=context())
    svc.execute_one(child.round_id)
    root = f"/api/soc/dev/corpus-workbench/rounds/{child.round_id}/comparison"
    response = http.get(root, params={"limit": 1})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 2 and len(result["items"]) == 1
    first = result["items"][0]
    assert first["before"]["run_id"] == f"RUN-{parent.round_id}-0"
    assert first["after"]["run_id"] == "NEW-0"
    second = http.get(root, params={"limit": 1, "offset": 1}).json()["items"][0]
    assert second["before"] is None and second["comparison_status"] == "new_sample"
    assert http.get(root, headers={"test-role": "user"}).status_code == 403
    assert http.get(root, params={"limit": 101}).status_code == 422
    assert len(calls) == 1  # Read requests never invoke either executor.
    assert http.get(f"/api/soc/dev/corpus-workbench/rounds/{parent.round_id}/comparison").status_code == 409


def test_selected_report_results_must_still_match_when_retest_is_prepared(tmp_path):
    import json

    from soc_agent.demo.corpus_retest_selection import build_retest_plan

    repo = repository(tmp_path)
    prepare(repo)
    svc = service(repo, [])
    parent = svc.create_round(command(), context=context())
    row = job_result_row(svc.store.list_round_items(parent.round_id).items[0].job)
    path = tmp_path / "old-report.json"
    path.write_text(
        json.dumps(
            {"schema_version": "soc.corpus_round_report.v1", "round": parent.model_dump(mode="json"), "source_identity": svc.store.get_experiment("EXP-test").source_identity, "rows": [{"alert_id": "0", "validation_tier": None, **row}]}
        ),
        encoding="utf-8",
    )
    plan = build_retest_plan(path, all_rows=True)
    svc.start(parent.round_id, context=context())
    svc.execute_one(parent.round_id)
    with pytest.raises(ValueError, match="report results changed"):
        svc.create_round(command(parent_round_id=parent.round_id, retest_provenance=plan.provenance), context=context())
    assert len(svc.store.list_rounds()) == 1
    row = job_result_row(svc.store.list_round_items(parent.round_id).items[0].job)
    path.write_text(
        json.dumps(
            {"schema_version": "soc.corpus_round_report.v1", "round": svc.store.get_round(parent.round_id).model_dump(mode="json"), "source_identity": svc.store.get_experiment("EXP-test").source_identity, "rows": [{"alert_id": "0", **row}]}
        ),
        encoding="utf-8",
    )
    current_plan = build_retest_plan(path, all_rows=True)
    child = svc.create_round(command(parent_round_id=parent.round_id, retest_provenance=current_plan.provenance), context=context())
    assert svc.store.list_round_items(child.round_id).items[0].job.metadata["comparison_baseline"]["result"]["run_id"] == row["run_id"]


@pytest.mark.parametrize(
    "old_status,new_status,old_handling,new_handling,expected",
    [
        ("completed", "completed", "transfer", "ignore", "handling_changed"),
        ("completed", "completed", "ignore", "ignore", "handling_unchanged"),
        ("failed", "completed", None, "ignore", "not_comparable"),
        ("completed", "queued", "ignore", None, "not_comparable"),
    ],
)
def test_comparison_status_does_not_treat_failure_as_same_effect(old_status, new_status, old_handling, new_handling, expected):
    from soc_agent.contracts.processing_jobs import ProcessingJobStatus
    from soc_agent.demo.corpus_round_comparison import compare_round_item

    previous = comparison_side({"status": old_status, "summary": {"recommended_handling": old_handling}})
    job = SimpleNamespace(
        job_id="J2",
        run_id="RUN2",
        status=ProcessingJobStatus(new_status),
        attempt_count=1,
        error_code=None,
        error_message=None,
        result_payload={"summary": {"recommended_handling": new_handling}},
        metadata={"comparison_baseline": {"result": previous}},
    )
    assert compare_round_item(SimpleNamespace(alert_id="1", group_id="G1", job=job))["comparison_status"] == expected
