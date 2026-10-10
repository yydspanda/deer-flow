import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from soc_agent.contracts.corpus_experiments import CorpusExperiment, CorpusExperimentMember
from soc_agent.contracts.corpus_rebalance import CorpusRebalanceFact, CorpusRebalancePolicy
from soc_agent.demo.corpus_experiment_cli import main
from soc_agent.demo.corpus_rebalance_plan import build_corpus_rebalance_plan


def saved_plan(holdout=2):
    parent = CorpusExperiment(
        experiment_id="EXP-parent", name="parent", plan_id="a" * 64, tenant_id="tenant", environment="dev-corpus-eval", source_identity={"sha256": "b" * 64}, member_count=8, created_by="operator", created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    members, facts = [], []
    for i in range(8):
        event_time = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)
        members.append(
            CorpusExperimentMember(
                alert_id=str(i),
                payload_hash=f"{i:064x}",
                source_index=i,
                group_id="static",
                sequence_number=i,
                position_in_group=i,
                batch="learning" if i < 2 else "validation",
                validation_tier=None if i < 2 else "main",
                event_time=event_time,
                reason="original",
            )
        )
        facts.append(
            CorpusRebalanceFact(
                alert_id=str(i),
                payload_hash=f"{i:064x}",
                run_id=f"RUN-{i}",
                profile_id="p",
                profile_version="13",
                feature_schema_version="v11",
                pattern_key="actual",
                occurrence_key=f"o-{i}",
                event_time=event_time,
                semantic_status="partial",
            )
        )
    return build_corpus_rebalance_plan(parent, members, facts, policy=CorpusRebalancePolicy(holdout_per_pattern=holdout))


def test_facts_prepare_does_not_start_without_explicit_start(capsys):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "POST" and request.url.path.endswith("/experiments/EXP-parent/rebalance/facts")
        assert json.loads(request.content) == {}
        assert request.headers["Idempotency-Key"].startswith("rebalance-facts-")
        return httpx.Response(201, json={"experiment_id": "EXP-facts", "parent_experiment_id": "EXP-parent", "rounds": [{"round_id": "ROUND-facts", "state": "prepared"}]})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["rebalance-facts", "--experiment", "EXP-parent"], client=client) == 0
    assert len(calls) == 1
    assert "EXP-facts" in capsys.readouterr().out


def test_facts_starts_all_prepared_rounds_then_watches_without_restart_completed(capsys):
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/rebalance/facts"):
            assert request.headers["Idempotency-Key"] == "facts-request"
            return httpx.Response(
                201, json={"experiment_id": "EXP-facts", "parent_experiment_id": "EXP-parent", "rounds": [{"round_id": "R1", "state": "prepared"}, {"round_id": "R2", "state": "prepared"}, {"round_id": "R3", "state": "completed"}]}
            )
        if request.url.path.endswith("/start"):
            assert json.loads(request.content) == {"concurrency": 16}
            return httpx.Response(202, json={"round_id": request.url.path.split("/")[-2], "state": "running"})
        assert request.method == "GET"
        return httpx.Response(200, json={"round": {"round_id": request.url.path.split("/")[-1], "state": "completed"}, "active_count": 0, "failed_count": 0})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["rebalance-facts", "--experiment", "EXP-parent", "--start", "--wait", "--concurrency", "16", "--request-key", "facts-request"], client=client) == 0
    assert [method for method, _ in calls[:3]] == ["POST", "POST", "POST"]
    assert not any(path.endswith("/R3/start") for _, path in calls)


def test_facts_wait_requires_start_before_any_http_request(capsys):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("invalid command must not call server")), base_url="http://localhost:2026") as client:
        assert main(["rebalance-facts", "--experiment", "EXP-parent", "--wait"], client=client) == 1
    assert "--start" in capsys.readouterr().err


def test_plan_is_private_immutable_artifact_and_apply_submits_it_without_starting(tmp_path, capsys):
    plan = saved_plan(holdout=1)
    target = tmp_path / "new" / "plan.json"
    calls = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/rebalance/plan"):
            assert body == {"facts_experiment_id": "EXP-facts", "policy": {"holdout_per_pattern": 1, "minimum_learning_occurrences": 5, "missing_facts": "block"}}
            return httpx.Response(200, json=plan.model_dump(mode="json"))
        assert request.url.path.endswith("/experiments/EXP-parent/rebalance/apply")
        assert request.headers["Idempotency-Key"].startswith("rebalance-apply-")
        assert body == {"facts_experiment_id": "EXP-facts", "experiment_id": "EXP-new", "name": "新阶段", "plan_id": plan.plan_id, "policy": plan.policy.model_dump(mode="json")}
        return httpx.Response(201, json={"experiment": {"experiment_id": "EXP-new"}, "imported_learning_runs": 2, "inherited_memories": 1, "counts": plan.counts})

    args = ["rebalance-plan", "--experiment", "EXP-parent", "--facts-experiment", "EXP-facts", "--holdout", "1", "--output", str(target)]
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(args, client=client) == 0
        assert target.stat().st_mode & 0o777 == 0o600
        assert main(args, client=client) == 1
        assert len(calls) == 1
        assert main(["rebalance-apply", "--plan", str(target), "--experiment", "EXP-new", "--name", "新阶段"], client=client) == 0
        assert main(["rebalance-apply", "--plan", str(target), "--experiment", "EXP-new", "--name", "新阶段"], client=client) == 0
    assert len(calls) == 3 and all(not request.url.path.endswith("/start") for request in calls)
    assert calls[-1].headers["Idempotency-Key"] == calls[-2].headers["Idempotency-Key"]
    result = json.loads(target.read_text(encoding="utf-8"))
    assert result["facts_experiment_id"] == "EXP-facts" and result["plan"]["plan_id"] == plan.plan_id
    assert "moved_count" in capsys.readouterr().out


@pytest.mark.parametrize("field,value", [("facts_experiment_id", "EXP-other"), ("plan", {})])
def test_apply_rejects_edited_wrapper_before_post(tmp_path, field, value):
    target = tmp_path / "plan.json"
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=saved_plan().model_dump(mode="json"))), base_url="http://localhost:2026") as client:
        assert main(["rebalance-plan", "--experiment", "EXP-parent", "--facts-experiment", "EXP-facts", "--output", str(target)], client=client) == 0
    wrapper = json.loads(target.read_text(encoding="utf-8"))
    wrapper[field] = value
    target.write_text(json.dumps(wrapper), encoding="utf-8")
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("edited artifact must not be submitted")), base_url="http://localhost:2026") as client:
        assert main(["rebalance-apply", "--plan", str(target), "--experiment", "EXP-new"], client=client) == 1


def test_plan_with_blockers_is_saved_but_cannot_be_applied(tmp_path):
    from soc_agent.utils.hashing import stable_hash

    plan = saved_plan().model_dump(mode="json", exclude={"plan_id"})
    plan["blockers"] = ["semantic_facts_incomplete:1"]
    plan["plan_id"] = stable_hash(plan)
    target = tmp_path / "blocked.json"
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=plan)), base_url="http://localhost:2026") as client:
        assert main(["rebalance-plan", "--experiment", "EXP-parent", "--facts-experiment", "EXP-facts", "--output", str(target)], client=client) == 0
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("blocked artifact must not apply")), base_url="http://localhost:2026") as client:
        assert main(["rebalance-apply", "--plan", str(target), "--experiment", "EXP-new"], client=client) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_export_carries_successor_stage_metadata_and_rejects_page_drift(tmp_path, monkeypatch, changed):
    exported = []
    monkeypatch.setattr("soc_agent.demo.corpus_experiment_cli.export_report", lambda report, _: exported.append(report))

    def handle(request):
        if request.url.path.endswith("/results"):
            offset = int(request.url.params.get("offset", "0"))
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "items": [{"alert_id": str(offset)}],
                    "source_identity": {"sha256": "fixed"},
                    "experiment": {"experiment_id": "EXP-new", "rebalance": {"parent_experiment_id": "EXP-old"}},
                    "stage_scope": {"batch_total": 2, "stage_complete": not (changed and offset)},
                },
            )
        return httpx.Response(200, json={"round": {"round_id": "R-new", "state": "completed", "version": 2}, "active_count": 0})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["export", "--round", "R-new", "--output-dir", str(tmp_path / "report")], client=client) == int(changed)
    if not changed:
        assert exported[0]["experiment"]["rebalance"]["parent_experiment_id"] == "EXP-old"
        assert exported[0]["stage_scope"] == {"batch_total": 2, "stage_complete": True}
    else:
        assert exported == []
