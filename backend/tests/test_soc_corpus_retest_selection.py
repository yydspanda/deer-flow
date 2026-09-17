import copy
import json

import httpx
import pytest

from soc_agent.demo.corpus_experiment_cli import main
from soc_agent.demo.corpus_retest_selection import build_retest_plan, read_retest_plan


def report():
    return {
        "schema_version": "soc.corpus_round_report.v1",
        "round": {"round_id": "R-old", "experiment_id": "EXP-test", "selection": {"batch": "validation", "scope": "all"}},
        "source_identity": {"sha256": "fixed-dataset"},
        "rows": [
            {"alert_id": "A1", "run_id": "RUN-old-1", "status": "completed", "rule_code": "R1", "group_id": "G1", "validation_tier": "main", "summary": {"memory_uses": [{"memory_id": "M1", "directive_applied": True}]}},
            {"alert_id": "A2", "run_id": "RUN-old-2", "status": "completed", "rule_code": "R1", "group_id": "G2", "validation_tier": "supplementary", "summary": {"memory_uses": [{"memory_id": "M1", "directive_applied": False}]}},
            {"alert_id": "A3", "run_id": None, "status": "failed", "rule_code": "R2", "group_id": "G3", "validation_tier": "main", "summary": {}},
            {"alert_id": "A4", "run_id": None, "status": "queued", "rule_code": "R1", "group_id": "G1", "validation_tier": "main", "summary": {}},
        ],
    }


def source(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report()), encoding="utf-8")
    return path


def test_select_actual_memory_uses_preserves_exploration_and_old_run_ids(tmp_path):
    path = source(tmp_path)
    plan = build_retest_plan(path, used_memory_ids=["M1"])
    assert plan.selection.alert_ids == ["A1", "A2"]
    assert plan.selection.scope == "all"
    assert plan.previous_run_ids == {"A1": "RUN-old-1", "A2": "RUN-old-2"}
    assert plan.validation_tiers == {"A1": "main", "A2": "supplementary"}
    assert plan.provenance.parent_round_id == "R-old"
    assert plan.provenance.source_identity == report()["source_identity"]
    assert len(plan.provenance.report_sha256) == 64
    assert build_retest_plan(path, failed=True).selection.alert_ids == ["A3"]
    assert build_retest_plan(path, used_memory_ids=["M1"], group_ids=["G2"]).selection.alert_ids == ["A2"]
    assert build_retest_plan(path, all_rows=True).selection.alert_ids == ["A1", "A2", "A3", "A4"]
    assert build_retest_plan(path, rule_codes=["R1"], alert_ids=["A1"]).selection.alert_ids == ["A1"]


def test_retest_plan_rejects_ambiguous_or_empty_selection_and_duplicate_source(tmp_path):
    path = source(tmp_path)
    with pytest.raises(ValueError, match="筛选"):
        build_retest_plan(path)
    with pytest.raises(ValueError, match="没有"):
        build_retest_plan(path, used_memory_ids=["unused-Memory-in-snapshot"])
    with pytest.raises(ValueError, match="不在"):
        build_retest_plan(path, alert_ids=["A404"])
    with pytest.raises(ValueError, match="同时"):
        build_retest_plan(path, all_rows=True, failed=True)
    bad = report()
    bad["rows"].append(copy.deepcopy(bad["rows"][0]))
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        build_retest_plan(path, all_rows=True)


def test_plan_cli_is_offline_private_and_does_not_overwrite(tmp_path, capsys):
    path, output = source(tmp_path), tmp_path / "plan.json"
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("planning must not call Gateway"))) as client:
        args = ["retest-plan", "--report", str(path), "--failed", "--output", str(output)]
        assert main(args, client=client) == 0
        assert main(args, client=client) == 1
    plan = read_retest_plan(output)
    assert plan.selection.alert_ids == ["A3"]
    assert output.stat().st_mode & 0o777 == 0o600
    assert "model_calls" in capsys.readouterr().out
    changed = json.loads(output.read_text(encoding="utf-8"))
    changed["selection"]["alert_ids"] = ["A1"]
    output.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="校验"):
        read_retest_plan(output)


def test_cli_creates_retest_with_fixed_membership_parent_and_provenance(tmp_path):
    output = tmp_path / "plan.json"
    assert main(["retest-plan", "--report", str(source(tmp_path)), "--used-memory", "M1", "--output", str(output)]) == 0
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/configuration"):
            return httpx.Response(200, json={"max_concurrency": 3, "defaults": {}})
        if request.url.path.endswith("/rounds"):
            body = json.loads(request.content)
            assert body["selection"]["alert_ids"] == ["A1", "A2"]
            assert body["selection"]["scope"] == "all"
            assert body["parent_round_id"] == "R-old"
            assert body["retest_provenance"]["report_sha256"] == read_retest_plan(output).provenance.report_sha256
            return httpx.Response(201, json={"round_id": "R-new"})
        assert request.url.path.endswith("/start")
        return httpx.Response(202, json={"round_id": "R-new", "state": "running"})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        args = ["validate", "--experiment", "EXP-test", "--selection-file", str(output)]
        assert main(args, client=client) == 0
        calls.clear()
        for extra in (["--group", "G1"], ["--parent-round", "R-other"], ["--scope", "reuse"]):
            assert main([*args, *extra], client=client) == 1
            assert calls == []
        assert main(["validate", "--experiment", "EXP-other", "--selection-file", str(output)], client=client) == 1
        assert calls == []


def test_report_selection_hash_and_plan_are_deterministic(tmp_path):
    path = source(tmp_path)
    first = build_retest_plan(path, used_memory_ids=["M1", "M1"], rule_codes=["R1"])
    second = build_retest_plan(path, rule_codes=["R1"], used_memory_ids=["M1"])
    assert first == second
