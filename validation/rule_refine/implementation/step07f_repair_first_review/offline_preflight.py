"""Real CSV budget/lineage check with scripted E replies, never real models."""

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def load(name, path):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(folder):
    return {
        str(p.relative_to(folder)): digest(p) for p in folder.rglob("*") if p.is_file()
    }


def main():
    os.umask(0o077)
    prior = load(
        "_repair_e_preflight",
        HERE.parent / "step07e_event_field_boundary/offline_preflight.py",
    )
    load("surge_materials", HERE.parent / "step06z_surge_recommendation/materials.py")
    load(
        "restricted_dataframe_pickle",
        ROOT / "validation/compact_zeus/shared/restricted_dataframe_pickle.py",
    )
    materials = load(
        "materials", HERE.parent / "step07b_real_csv_acceptance/materials.py"
    )
    runner = load(
        "_repair_csv_runner", HERE.parent / "step07b_real_csv_acceptance/runner.py"
    )
    tool_path = HERE / "payload/test/rule_refine/check_surge_review_repair.py"
    tool_hash = digest(tool_path)
    tool = load(
        "_repair_saved_tool",
        tool_path,
    )
    current = ROOT / "validation/rule_refine/datas/20261102175803453.csv"
    history = ROOT / "datas/source/full_alert_2026_month_forth_sample_200.pkl"
    rules = ROOT / "validation/rule_refine/202610221741结果集.csv"
    sources = {str(p.relative_to(ROOT)): digest(p) for p in (current, history, rules)}
    with tempfile.TemporaryDirectory(prefix="repair-review-preflight-") as temp:
        root = Path(temp) / "project"
        root.mkdir()
        prior.stage_project(root)
        modules = runner.modules(root)

        def rules_factory():
            return modules("csv_config_source").CsvRuleConfigSource(rules)

        case, signal, audit = materials.prepare_case(
            root, current, history, modules, rules_factory()
        )
        old_requests = []

        def scripted_reply(request):
            is_generation = request["phase"] == "generate"
            current_samples = request["context"]["current_samples"]
            value = next(
                log["detail_process_name"]
                for log in current_samples[0]["raw_logs"]
                if isinstance(log.get("detail_process_name"), str)
            )
            answer = {
                "status": "proposal" if is_generation else "insufficient_evidence",
                "issue": "SCRIPTED_CONTROL_NOT_AI_EVIDENCE",
                "recommendation": "SCRIPTED_CONTROL_NOT_AI_EVIDENCE",
                "certainty": "Not assessed",
                "filter": {
                    "topic": case["rule"]["topic"],
                    "A": {
                        "name": "detail_process_name",
                        "operator": "equal",
                        "value": value,
                    },
                    "expression": "A",
                }
                if is_generation
                else None,
                "sample_alert_ids": [s["alert_id"] for s in current_samples]
                if is_generation
                else [],
                "review_notes": "SCRIPTED_CONTROL_NOT_AI_EVIDENCE",
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

        def old_transport(request):
            old_requests.append(copy.deepcopy(request))
            return scripted_reply(request)

        source = Path(temp) / "source-e"
        processor = modules("surge_processor").SurgeProcessor(
            source,
            load_case=lambda _: case,
            load_rules=rules_factory,
            generate=old_transport,
            model="scripted-control-no-sdk",
            budget=modules("surge_budget").ContextBudget(),
            operator_context=runner.OPERATOR_CONTEXT,
        )
        original = processor.process(signal)
        assert original["status"] == "not_applicable" and len(old_requests) == 2
        frozen = snapshot(source)
        source_hashes = {
            name: digest(root / prior.MODULE / name)
            for name in ("surge_ai_review.py", "surge_processor.py")
        }
        (root / prior.MODULE / "surge_ai_review.py").write_bytes(
            (HERE / "payload" / prior.MODULE / "surge_ai_review.py").read_bytes()
        )
        (root / "config").mkdir()
        (root / "config/rule_refine.analysis.local.json").write_text(
            json.dumps({"enabled": True, "model": "scripted-control-no-sdk"}),
            encoding="utf-8",
        )
        (root / "config/rule_refine.surge.local.json").write_text(
            json.dumps({"enabled": True}), encoding="utf-8"
        )
        prepared = tool.prepare(root, source, {"env_profile": "LOCAL"})
        new = prepared["request"]
        old = old_requests[1]
        for key in (
            "current_samples",
            "history_samples",
            "coverage",
            "draft",
            "operator_context",
        ):
            assert new["context"][key] == old["context"][key]
        assert "SCRIPTED_CONTROL_NOT_AI_EVIDENCE" not in json.dumps(new["messages"])
        attempts = []

        def one_review(request):
            attempts.append(request)
            assert request["phase"] == "review"
            return scripted_reply(request)

        result = tool.run(prepared, Path(temp) / "new-review", one_review)
        assert len(attempts) == 1 and result["status"] == "not_applicable"
        assert snapshot(source) == frozen
        assert all(digest(ROOT / name) == value for name, value in sources.items())
        assert digest(tool_path) == tool_hash
        summary = {
            "check": "real_csv_saved_review_repair_preflight",
            "status": "passed",
            "execution_mode": "scripted_offline_control",
            "response_origin": "scripted_controls_not_internal_saved_model_answers",
            "model_calls": 0,
            "sdk_request_calls": 0,
            "redis_calls": 0,
            "business_database_calls": 0,
            "business_database_rows_written": 0,
            "scripted_source_calls": 2,
            "scripted_repair_review_calls": 1,
            "current_alerts": len(case["current_samples"]),
            "current_logs": sum(len(s["raw_logs"]) for s in case["current_samples"]),
            "history_alerts": len(case["history_samples"]),
            "source_generation_current": len(
                old_requests[0]["context"]["current_samples"]
            ),
            "review_current": len(new["context"]["current_samples"]),
            "review_history": len(new["context"]["history_samples"]),
            "old_prompt_version": old["prompt_version"],
            "new_prompt_version": new["prompt_version"],
            "old_input_bytes": old["input_bytes"],
            "new_input_bytes": new["input_bytes"],
            "old_estimated_tokens": old["estimated_input_tokens"],
            "new_estimated_tokens": new["estimated_input_tokens"],
            "input_token_budget": new["input_token_budget"],
            "model_output_token_limit": None,
            "frozen_chain_verified_after_upgrade": True,
            "same_review_samples": True,
            "original_report_bytes_unchanged": True,
            "source_hashes": sources,
            "old_runtime_hashes": source_hashes,
            "runtime_sha256": digest(root / prior.MODULE / "surge_ai_review.py"),
            "tool_sha256": tool_hash,
            "temporary_database_removed": audit["temporary_database_removed"],
            "filter_replay_performed": False,
            "semantic_quality_verified": False,
            "automatic_apply": False,
        }
    folder = Path(
        tempfile.mkdtemp(
            prefix="repair-review-preflight-", dir=ROOT / "validation/rule_refine/datas"
        )
    )
    (folder / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "summary": str(folder / "summary.json"),
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
