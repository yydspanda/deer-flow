"""Real export recall plus scripted field-boundary controls; never calls a model."""

import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MODULE = "my_workflows/zeus/rule_refine/"


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint_value(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def stage_project(root):
    previous = load_file(
        "event_preflight_previous_builder",
        HERE.parent / "step07d_exclusion_scope_fix/build_command.py",
    )
    packet = previous.contents()
    for name, digest in packet["dependencies"].items():
        if name.startswith(MODULE):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(previous.previous_source(name, digest))
    for name, entry in packet["files"].items():
        if name.startswith(MODULE):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entry["content"], encoding="utf-8")
    for source in (HERE / "payload" / MODULE).glob("*.py"):
        path = root / MODULE / source.name
        path.write_bytes(source.read_bytes())


def main():
    os.umask(0o077)
    started = time.monotonic()
    current = ROOT / "validation/rule_refine/datas/20261102175803453.csv"
    history = ROOT / "datas/source/full_alert_2026_month_forth_sample_200.pkl"
    rules = ROOT / "validation/rule_refine/202610221741结果集.csv"
    sources = {
        str(path.relative_to(ROOT)): fingerprint(path)
        for path in (current, history, rules)
    }
    load_file(
        "surge_materials",
        HERE.parent / "step06z_surge_recommendation/materials.py",
    )
    load_file(
        "restricted_dataframe_pickle",
        ROOT / "validation/compact_zeus/shared/restricted_dataframe_pickle.py",
    )
    materials = load_file(
        "materials", HERE.parent / "step07b_real_csv_acceptance/materials.py"
    )
    runner = load_file(
        "event_preflight_real_csv_runner",
        HERE.parent / "step07b_real_csv_acceptance/runner.py",
    )
    with tempfile.TemporaryDirectory(prefix="event-field-preflight-runtime-") as temp:
        root = Path(temp) / "project"
        root.mkdir()
        stage_project(root)
        load = runner.modules(root)

        def rules_factory():
            return load("csv_config_source").CsvRuleConfigSource(rules)

        case, signal, audit = materials.prepare_case(
            root, current, history, load, rules_factory()
        )
        original_hash = fingerprint_value(case)
        prepared = {
            "load": load,
            "case": case,
            "signal": signal,
            "analysis": {"model": "scripted_offline_control_not_model"},
            "budget": load("surge_budget").ContextBudget(),
            "rules_factory": rules_factory,
        }
        scenarios = {}
        for name, repair in (
            ("metadata_rejected", False),
            ("review_repairs_metadata", True),
        ):
            requests = []

            def scripted_transport(request):
                requests.append(copy.deepcopy(request))
                context = request["context"]
                fields = context["candidate_field_names"]
                assert "detail_hit_rule_name" not in fields
                assert "event_content" in fields
                if context["phase"] == "review":
                    assert (
                        context["draft_validation_issues"][0]["code"]
                        == "condition_uses_detection_metadata"
                    )
                sample = context["current_samples"][0]
                # A deliberately scripted control, not an AI proposal or a
                # claim that this event substring is suitable for exclusion.
                field = (
                    "event_content"
                    if repair and context["phase"] == "review"
                    else "detail_hit_rule_name"
                )
                value = next(
                    log[field]
                    for log in sample["raw_logs"]
                    if isinstance(log.get(field), str) and log[field]
                )
                answer = {
                    "status": "proposal",
                    "issue": "Scripted offline pipeline control; not an AI conclusion.",
                    "recommendation": "Do not apply: scripted field-role validation only.",
                    "certainty": "Not assessed.",
                    "filter": {
                        "topic": case["rule"]["topic"],
                        "A": {"name": field, "operator": "contain", "value": value},
                        "expression": "A",
                    },
                    "sample_alert_ids": [
                        item["alert_id"] for item in context["current_samples"]
                    ],
                    "review_notes": "SCRIPTED_TEXT_NOT_MODEL_EVIDENCE",
                    "sample_request": None,
                }
                return {
                    "content": json.dumps(answer, ensure_ascii=False),
                    "transport": {
                        "transport_mode": "scripted_offline",
                        "sdk_request_calls": 0,
                        "response_received": True,
                        "finish_reason": "stop",
                        "error_code": None,
                    },
                }

            outcome = runner.process(prepared, Path(temp) / name, scripted_transport)
            assert len(requests) == 2
            assert outcome["recommendation_ready"] is repair
            assert outcome["status"] == ("received" if repair else "manual_review")
            folder = Path(outcome["output_dir"])
            phases = {}
            for phase, request in zip(("generate", "review"), requests):
                context = request["context"]
                selection = json.loads(
                    (folder / phase / "selection.json").read_text(encoding="utf-8")
                )
                parsed = json.loads(
                    (folder / phase / "parsed.json").read_text(encoding="utf-8")
                )
                assert (
                    request["estimated_input_tokens"]
                    <= prepared["budget"].input_token_budget
                )
                assert context["history_samples"] == load("surge_ai_review")._samples(
                    case["history_samples"]
                )
                for key in ("current_samples", "history_samples"):
                    originals = {sample["alert_id"]: sample for sample in case[key]}
                    assert all(
                        originals[sample["alert_id"]] == sample
                        for sample in selection[key]
                    )
                assert (
                    "SCRIPTED_TEXT_NOT_MODEL_EVIDENCE"
                    not in request["messages"][1]["content"]
                )
                phases[phase] = {
                    "selection": selection["selection"],
                    "input_bytes": request["input_bytes"],
                    "estimated_input_tokens": request["estimated_input_tokens"],
                    "prompt_version": request["prompt_version"],
                    "field_usage_policy_version": context["field_usage_policy_version"],
                    "candidate_field_count": len(context["candidate_field_names"]),
                    "context_only_field_names": [
                        item["name"]
                        for item in context["field_usage"]["context_only_fields"]
                    ],
                    "validation_issue_codes": [
                        item["code"] for item in parsed["reply"]["validation_issues"]
                    ],
                    "recommendation_ready": parsed["reply"]["recommendation_ready"],
                }
            report = (folder / "REPORT.md").read_text(encoding="utf-8")
            scenarios[name] = {
                "status": outcome["status"],
                "recommendation_ready": outcome["recommendation_ready"],
                "scripted_transport_calls": len(requests),
                "model_calls": 0,
                "sdk_request_calls": 0,
                "phases": phases,
                "report_created": bool(report),
                "report_is_scripted_control_not_ai_evidence": True,
            }
            print(
                json.dumps(
                    {"scenario": name, "status": outcome["status"], "model_calls": 0}
                )
            )
        assert fingerprint_value(case) == original_hash
        assert all(
            fingerprint(ROOT / name) == digest for name, digest in sources.items()
        )
        summary = {
            "check": "real_csv_event_field_boundary_preflight",
            "status": "passed",
            "execution_mode": "offline_scripted_control",
            "material_source": "real_csv_and_real_small_pickle",
            "model_calls": 0,
            "sdk_request_calls": 0,
            "redis_calls": 0,
            "business_database_calls": 0,
            "business_database_rows_written": 0,
            "database_scope": "disposable_sqlite_from_exports_no_project_database_present",
            "temporary_database_removed": audit["temporary_database_removed"],
            "application_code_updated": False,
            "current_alerts": len(case["current_samples"]),
            "current_logs": sum(
                len(sample["raw_logs"]) for sample in case["current_samples"]
            ),
            "history_alerts": len(case["history_samples"]),
            "history_logs": sum(
                len(sample["raw_logs"]) for sample in case["history_samples"]
            ),
            "history_gap_seconds": audit["history_gap_seconds"],
            "historical_pickle_rows": next(
                item["file_rows"]
                for item in audit["sources"]
                if item["kind"] == "historical_small_pickle"
            ),
            "input_token_budget": prepared["budget"].input_token_budget,
            "model_output_token_limit": None,
            "case_and_selected_original_logs_unchanged": True,
            "source_hashes_unchanged": True,
            "source_hashes": sources,
            "runtime_source_hashes": {
                source.name: fingerprint(source)
                for source in (root / MODULE).glob("surge_*.py")
            },
            "scenarios": scenarios,
            "filter_replay_performed": False,
            "filter_coverage_measured": False,
            "semantic_quality_verified": False,
            "automatic_apply": False,
            "total_elapsed_seconds": round(time.monotonic() - started, 3),
        }
    output = Path(
        tempfile.mkdtemp(
            prefix="event-field-preflight-", dir=ROOT / "validation/rule_refine/datas"
        )
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "summary": str(output / "summary.json"),
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
