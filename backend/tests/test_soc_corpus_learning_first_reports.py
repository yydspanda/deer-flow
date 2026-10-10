"""Static learning-first sampling reports keep history labels out of evaluation."""

import csv
import json
from types import SimpleNamespace

import httpx
import pytest
from test_soc_corpus_experiment_api import client
from test_soc_corpus_experiments import context
from test_soc_corpus_rebalance_reports import report

from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiment_cli import main
from soc_agent.demo.corpus_experiment_reports import compare_reports, export_report


def sampling_policy():
    return {
        "schema_version": "soc.corpus_group_time_split.v3",
        "grouping_basis": "static_workbench_index",
        "split_method": "per_group_time_80_20_main_learning_first",
        "max_learning_per_group": None,
    }


def static_report():
    value = report()
    value.update(experiment={"experiment_id": "EXP-static", "rebalance": None}, report_mode="memory_applicability", sampling_policy=sampling_policy())
    value["stage_scope"].update(learning_total=12, validation_total=3)
    return value


def test_static_learning_first_export_uses_applicability_without_inventing_semantic_split(tmp_path):
    export_report(static_report(), tmp_path / "stage")
    saved = json.loads((tmp_path / "stage/report.json").read_text(encoding="utf-8"))
    assert saved["stage_conclusion_allowed"] is True
    assert saved["report_scope"] == "stage"
    assert saved["experiment"]["rebalance"] is None
    assert saved["metrics"]["main"]["known_experience"]["ignore"] == 1
    assert saved["metrics"]["main"]["no_history_experience"]["transfer"] == 1
    assert "historical_handling_agreement" not in json.dumps(saved)
    assert "must-not-export" not in json.dumps(saved)
    assert all("label" not in row for row in saved["rows"])
    with (tmp_path / "stage/alerts.csv").open(encoding="utf-8-sig") as stream:
        assert "expected_handling" not in csv.DictReader(stream).fieldnames
    rendered = (tmp_path / "stage/REPORT.md").read_text(encoding="utf-8")
    assert "静态分组" in rendered and "80%" in rendered
    assert "补充验证保持不变" in rendered
    assert "第一批 12 条，第二批 3 条" in rendered
    assert "无历史经验" in rendered
    assert "按真实核心行为重新分配" not in rendered
    assert "保留既有数据库" not in rendered
    assert "历史处置一致率" not in rendered


@pytest.mark.parametrize("defect", ["incomplete", "partial_round", "failed", "snapshot", "duplicate", "facts", "learning", "main_only"])
def test_static_stage_requires_all_held_out_members(tmp_path, defect):
    value = static_report()
    if defect == "incomplete":
        value["stage_scope"]["stage_complete"] = False
    elif defect == "partial_round":
        value["stage_scope"]["batch_total"] = 4
    elif defect == "failed":
        value["rows"][0]["status"] = "failed"
    elif defect == "snapshot":
        value["rows"][0]["snapshot_changed_during_run"] = True
    elif defect == "duplicate":
        value["rows"][0]["alert_id"] = "2"
    elif defect == "facts":
        value["round"]["purpose"] = "facts"
    elif defect == "learning":
        value["round"]["selection"] = {"batch": "learning", "scope": "reuse"}
    else:
        value["round"]["selection"]["scope"] = "reuse"
    export_report(value, tmp_path / defect)
    saved = json.loads((tmp_path / defect / "report.json").read_text(encoding="utf-8"))
    assert saved["stage_conclusion_allowed"] is False
    assert saved["report_scope"] == "round_diagnostic"
    assert "仅诊断" in (tmp_path / defect / "REPORT.md").read_text(encoding="utf-8")


def test_static_report_comparison_stays_label_blind():
    value = static_report()
    comparison = compare_reports(value, {**value, "round": {**value["round"], "round_id": "next"}})
    assert set(comparison["before_metrics"]) == {"main", "supplementary"}
    assert "historical_handling_agreement" not in json.dumps(comparison)


def test_static_results_bind_sampling_metadata_and_do_not_read_history(tmp_path):
    http, application, _ = client(tmp_path)
    service = application.service
    experiment = service.store.get_experiment("EXP-test")
    round_ = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", scope="all"), execution_limit=100, memory_mode="none"), context=context())
    service.start(round_.round_id, context=context())
    while service.execute_one(round_.round_id):
        pass

    def forbidden_label(*args, **kwargs):
        raise AssertionError("new learning-first report read operational labels")

    application.workbench = SimpleNamespace(batch_plan_id=experiment.plan_id, batch_plan=SimpleNamespace(**sampling_policy()), experiment_label=forbidden_label)
    response = http.get(f"/api/soc/dev/corpus-workbench/rounds/{round_.round_id}/results?limit=1")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["report_mode"] == "memory_applicability"
    assert value["sampling_policy"] == sampling_policy()
    assert value["stage_scope"] == {"batch_total": 2, "learning_total": 10, "validation_total": 2, "stage_complete": True}
    assert value["experiment"]["rebalance"] is None
    assert "label" not in value["items"][0]
    application.workbench.batch_plan_id = "other-plan"
    assert http.get(f"/api/soc/dev/corpus-workbench/rounds/{round_.round_id}/results").status_code == 409


@pytest.mark.parametrize("changed", [False, True])
def test_cli_preserves_sampling_mode_and_rejects_policy_drift(tmp_path, monkeypatch, changed):
    exported = []
    monkeypatch.setattr("soc_agent.demo.corpus_experiment_cli.export_report", lambda value, _: exported.append(value))

    def handle(request):
        if request.url.path.endswith("/results"):
            offset = int(request.url.params.get("offset", "0"))
            policy = {**sampling_policy(), "max_learning_per_group": 10 if changed and offset else None}
            return httpx.Response(200, json={"total": 2, "items": [{"alert_id": str(offset)}], "source_identity": {}, "report_mode": "memory_applicability", "sampling_policy": policy})
        return httpx.Response(200, json={"round": {"round_id": "R-static", "state": "completed", "version": 2}, "active_count": 0})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(["export", "--round", "R-static", "--output-dir", str(tmp_path / "report")], client=http) == int(changed)
    if changed:
        assert exported == []
    else:
        assert exported[0]["report_mode"] == "memory_applicability"
        assert exported[0]["sampling_policy"] == sampling_policy()
