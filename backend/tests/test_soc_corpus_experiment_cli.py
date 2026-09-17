import httpx
import pytest

from soc_agent.demo.corpus_experiment_cli import main


def test_learning_cli_uses_server_defaults_and_preserves_scope_and_budget(capsys):
    seen = []

    def handle(request):
        import json

        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path.endswith("/configuration"):
            return httpx.Response(200, json={"max_concurrency": 3, "defaults": {"normalization_review_mode": "apply", "tenant_policy_enabled": False}})
        if request.url.path.endswith("/rounds"):
            body = seen[-1][2]
            assert body["selection"]["group_ids"] == ["group-a"]
            assert body["selection"]["rule_codes"] == ["RULE-1"]
            assert body["execution_limit"] == 10
            assert body["options"]["normalization_review_mode"] == "apply"
            assert request.headers["Idempotency-Key"] == "reliable-submit"
            return httpx.Response(201, json={"round_id": "ROUND-test", "state": "prepared"})
        return httpx.Response(202, json={"round_id": "ROUND-test", "state": "running"})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(["learn", "--experiment", "EXP-test", "--group", "group-a", "--rule", "RULE-1", "--limit", "10", "--request-key", "reliable-submit"], client=http) == 0
    assert seen[-1][1].endswith("/rounds/ROUND-test/start")
    assert "ROUND-test" in capsys.readouterr().out


def test_dry_run_does_not_create_or_start_a_round():
    seen = []

    def handle(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/configuration"):
            return httpx.Response(200, json={"max_concurrency": 3, "defaults": {}})
        assert request.url.path.endswith("/selection")
        return httpx.Response(200, json={"total": 50, "items": []})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(["validate", "--experiment", "EXP-test", "--scope", "explore", "--dry-run"], client=http) == 0
    assert all("rounds" not in path for path in seen)


def test_resume_is_control_only_and_auth_error_is_readable(capsys):
    def handle(request):
        assert request.url.path.endswith("/rounds/R-test/start")
        return httpx.Response(403, json={"detail": "Administrator required"})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(["resume", "--round", "R-test", "--limit", "50"], client=http) == 1
    assert "Administrator required" in capsys.readouterr().err


@pytest.mark.parametrize("change_version", [False, True])
def test_export_ignores_live_clock_but_rejects_state_change(tmp_path, change_version):
    progress_reads = 0

    def handle(request):
        nonlocal progress_reads
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json={"total": 0, "items": [], "source_identity": {"hash": "fixed"}})
        progress_reads += 1
        return httpx.Response(
            200,
            json={
                "round": {"round_id": "R-test", "state": "paused", "version": progress_reads if change_version else 2},
                "active_count": 0,
                "timing": {"elapsed_ms": progress_reads * 1000},
            },
        )

    target = tmp_path / "report"
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(["export", "--round", "R-test", "--output-dir", str(target)], client=http) == (1 if change_version else 0)
    assert target.exists() is not change_version


def test_draft_plan_uses_only_saved_explicit_verdicts_and_submission_is_repeatable(tmp_path, capsys):
    import json

    calls = []
    revision = "d" * 64

    def handle(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "total": 3,
                    "items": [
                        {"candidate_id": "MC-ready", "candidate_revision": revision, "editable": True, "stale": False, "draft": {"version": 2, "content": {"reviewer_verdict": "false_positive"}}},
                        {"candidate_id": "MC-no-verdict", "candidate_revision": revision, "editable": True, "stale": False, "draft": None},
                        {"candidate_id": "MC-edited", "candidate_revision": revision, "editable": True, "stale": False, "draft": {"version": 4, "content": {"reviewer_verdict": "true_positive", "conclusion": "已编辑经验"}}},
                    ],
                },
            )
        body = json.loads(request.content)
        assert body == {"commands": [{"candidate_id": "MC-ready", "expected_version": 2, "candidate_revision": revision, "regenerate": False}]}
        return httpx.Response(202, json={"items": [{"job_id": "JOB-draft", "status": "queued"}]})

    path = tmp_path / "draft-plan.json"
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(["draft-plan", "--experiment", "EXP-test", "--output", str(path)], client=http) == 0
        assert len(calls) == 1 and calls[0].method == "GET"
        assert path.stat().st_mode & 0o777 == 0o600
        assert main(["draft-candidates", "--plan", str(path)], client=http) == 0
        assert main(["draft-candidates", "--plan", str(path)], client=http) == 0
    assert calls[1].headers["Idempotency-Key"] == calls[2].headers["Idempotency-Key"]
    assert calls[1].content == calls[2].content
    assert "未保存运营最终判断" in capsys.readouterr().out


def test_draft_plan_without_operator_selection_never_creates_a_job(tmp_path):
    def handle(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"total": 1, "items": [{"candidate_id": "MC-new", "draft": None}]})

    path = tmp_path / "empty.json"
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(["draft-plan", "--experiment", "EXP-test", "--output", str(path)], client=http) == 1
    assert not path.exists()


@pytest.mark.parametrize("changed", [False, True])
def test_draft_export_is_read_only_and_rejects_changed_jobs(tmp_path, changed):
    reads = 0

    def handle(request):
        nonlocal reads
        reads += 1
        assert request.method == "GET"
        assert request.url.path.endswith("/experiments/EXP1/draft-jobs")
        return httpx.Response(200, json={"items": [{"job_id": "J1", "status": "completed", "version": reads if changed else 4, "attempt_count": 1}]})

    target = tmp_path / "draft-report"
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(["draft-export", "--experiment", "EXP1", "--output-dir", str(target)], client=http) == (1 if changed else 0)
    assert target.exists() is not changed
