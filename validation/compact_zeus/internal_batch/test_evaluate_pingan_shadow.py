from __future__ import annotations

import hashlib
import json
from pathlib import Path

from validation.compact_zeus.internal_batch.evaluate_pingan_shadow import (
    ShadowAcceptanceMode,
    evaluate_shadow_batches,
    main,
)


def test_evaluate_shadow_batches_passes_real_paired_five_row_gate(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path)

    report = evaluate_shadow_batches(
        runtime_batch_dir=case["runtime_dir"],
        investigation_batch_dir=case["investigation_dir"],
        composition_path=case["composition_path"],
        action_config_paths=[case["action_config_path"]],
        extensions_config_path=case["extensions_config_path"],
        ramp_stage="5",
        acceptance_mode=ShadowAcceptanceMode.INTERNAL_REAL,
    )

    assert report["gate_status"] == "passed"
    assert report["blocking_failure_ids"] == []
    assert report["configuration"]["selected_routes"] == ["asset.locate"]
    assert report["configuration"]["asset_lookup_disabled"] is True
    shadow = report["metrics"]["investigation_shadow"]
    assert shadow["provider_invocation_count"] == 5
    assert shadow["provider_hit_rate"] == 1.0
    assert shadow["effective_evidence_rate"] == 1.0
    assert shadow["routes"]["asset.locate"]["real_result_count"] == 5
    assert shadow["unauthorized_side_effect_counts"] == {
        "auto_close_allowed": 0,
        "base_run_mutation": 0,
        "confirmed_memory_write_allowed": 0,
        "high_risk_actions_allowed": 0,
    }
    assert report["claims"]["model_accuracy_evaluated"] is False
    assert report["claims"]["automatic_expansion_allowed"] is False
    assert report["claims"]["real_provider_evidence"] is True
    assert report["claims"]["closes_real_provider_gate"] is False
    attention_codes = {item["code"] for item in report["review_attention"]}
    assert "llm_monetary_cost_not_measured" in attention_codes
    assert "provider_cost_not_measured" in attention_codes
    assert str(tmp_path) not in json.dumps(report)


def test_evaluate_shadow_batches_passes_external_mock_rehearsal_without_real_claims(
    tmp_path: Path,
) -> None:
    case = _build_case(
        tmp_path,
        acceptance_mode=ShadowAcceptanceMode.EXTERNAL_SIMULATION,
    )

    report = evaluate_shadow_batches(
        runtime_batch_dir=case["runtime_dir"],
        investigation_batch_dir=case["investigation_dir"],
        composition_path=case["composition_path"],
        action_config_paths=[case["action_config_path"]],
        extensions_config_path=case["extensions_config_path"],
        ramp_stage="5",
        acceptance_mode=ShadowAcceptanceMode.EXTERNAL_SIMULATION,
    )

    assert report["gate_status"] == "passed"
    assert report["evidence_class"] == "simulated"
    assert report["configuration"]["provider_modes"] == {"pingan_asset": "fake"}
    shadow = report["metrics"]["investigation_shadow"]
    assert shadow["mock_result_count"] == 5
    assert shadow["real_result_count"] == 0
    assert report["claims"]["external_simulation_passed"] is True
    assert report["claims"]["internal_real_gate_passed"] is False
    assert report["claims"]["real_provider_evidence"] is False
    assert report["claims"]["closes_real_provider_gate"] is False
    assert report["claims"]["next_stage"] == "external_simulation_50"


def test_external_rehearsal_without_provider_hits_requires_attention(
    tmp_path: Path,
) -> None:
    case = _build_case(
        tmp_path,
        acceptance_mode=ShadowAcceptanceMode.EXTERNAL_SIMULATION,
    )
    for item_path in (case["investigation_dir"] / "items").glob("*.json"):
        item = json.loads(item_path.read_text(encoding="utf-8"))
        report = item["investigation_shadow_report"]
        report["success_count"] = 0
        report["not_found_count"] = 1
        report["routes"][0]["success_count"] = 0
        report["routes"][0]["not_found_count"] = 1
        _write_json(item_path, item)

    report = evaluate_shadow_batches(
        runtime_batch_dir=case["runtime_dir"],
        investigation_batch_dir=case["investigation_dir"],
        composition_path=case["composition_path"],
        action_config_paths=[case["action_config_path"]],
        extensions_config_path=case["extensions_config_path"],
        ramp_stage="5",
        acceptance_mode=ShadowAcceptanceMode.EXTERNAL_SIMULATION,
    )

    assert report["gate_status"] == "passed"
    attention_codes = {item["code"] for item in report["review_attention"]}
    assert "provider_hit_path_not_observed" in attention_codes


def test_external_mock_artifacts_cannot_pass_internal_real_gate(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        acceptance_mode=ShadowAcceptanceMode.EXTERNAL_SIMULATION,
    )

    report = evaluate_shadow_batches(
        runtime_batch_dir=case["runtime_dir"],
        investigation_batch_dir=case["investigation_dir"],
        composition_path=case["composition_path"],
        action_config_paths=[case["action_config_path"]],
        extensions_config_path=case["extensions_config_path"],
        ramp_stage="5",
        acceptance_mode=ShadowAcceptanceMode.INTERNAL_REAL,
    )

    assert report["gate_status"] == "failed"
    assert {
        "expected_result_composition",
        "expected_provider_modes",
        "expected_result_reports",
        "no_mock_results",
        "real_provider_observed",
        "configured_route_real_coverage",
    }.issubset(report["blocking_failure_ids"])
    assert report["claims"]["real_provider_evidence"] is False
    assert report["claims"]["closes_real_provider_gate"] is False


def test_evaluate_shadow_batches_fails_when_mock_or_side_effect_enters_report(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path)
    item_path = sorted((case["investigation_dir"] / "items").glob("*.json"))[0]
    item = json.loads(item_path.read_text(encoding="utf-8"))
    item["investigation_shadow_report"]["routes"][0]["real_result_count"] = 0
    item["investigation_shadow_report"]["routes"][0]["mock_result_count"] = 1
    item["investigation_shadow_report"]["confirmed_memory_write_allowed"] = True
    _write_json(item_path, item)

    report = evaluate_shadow_batches(
        runtime_batch_dir=case["runtime_dir"],
        investigation_batch_dir=case["investigation_dir"],
        composition_path=case["composition_path"],
        action_config_paths=[case["action_config_path"]],
        extensions_config_path=case["extensions_config_path"],
        ramp_stage="5",
        acceptance_mode=ShadowAcceptanceMode.INTERNAL_REAL,
    )

    assert report["gate_status"] == "failed"
    assert "no_mock_results" in report["blocking_failure_ids"]
    assert "zero_unauthorized_side_effects" in report["blocking_failure_ids"]


def test_evaluate_shadow_batches_rejects_development_asset_lookup_route(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path, route="asset.lookup")

    report = evaluate_shadow_batches(
        runtime_batch_dir=case["runtime_dir"],
        investigation_batch_dir=case["investigation_dir"],
        composition_path=case["composition_path"],
        action_config_paths=[case["action_config_path"]],
        extensions_config_path=case["extensions_config_path"],
        ramp_stage="5",
        acceptance_mode=ShadowAcceptanceMode.INTERNAL_REAL,
    )

    assert report["gate_status"] == "failed"
    assert "asset_lookup_disabled" in report["blocking_failure_ids"]


def test_evaluate_shadow_batches_detects_pre_llm_projection_drift(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path)
    item_path = sorted((case["investigation_dir"] / "items").glob("*.json"))[0]
    item = json.loads(item_path.read_text(encoding="utf-8"))
    item["analysis_run"]["fact_reconstruction"]["warnings"] = ["unexpected drift"]
    _write_json(item_path, item)

    report = evaluate_shadow_batches(
        runtime_batch_dir=case["runtime_dir"],
        investigation_batch_dir=case["investigation_dir"],
        composition_path=case["composition_path"],
        action_config_paths=[case["action_config_path"]],
        extensions_config_path=case["extensions_config_path"],
        ramp_stage="5",
        acceptance_mode=ShadowAcceptanceMode.INTERNAL_REAL,
    )

    assert report["gate_status"] == "failed"
    assert "deterministic_runtime_compatibility" in report["blocking_failure_ids"]
    assert (
        report["metrics"]["paired_compatibility"][
            "deterministic_projection_mismatch_count"
        ]
        == 1
    )


def test_shadow_evaluator_cli_writes_private_secret_free_report(
    tmp_path: Path,
    capsys,
) -> None:
    case = _build_case(tmp_path)
    report_path = tmp_path / "reports/pi-01e-five.json"

    exit_code = main(
        [
            "--runtime-batch-dir",
            str(case["runtime_dir"]),
            "--investigation-batch-dir",
            str(case["investigation_dir"]),
            "--enrichment-composition",
            str(case["composition_path"]),
            "--enrichment-action-config",
            str(case["action_config_path"]),
            "--enrichment-extensions-config",
            str(case["extensions_config_path"]),
            "--acceptance-mode",
            "internal_real",
            "--ramp-stage",
            "5",
            "--report-path",
            str(report_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["gate_status"] == "passed"
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["inputs"]["raw_payloads_included"] is False
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert report_path.parent.stat().st_mode & 0o777 == 0o700


def _build_case(
    tmp_path: Path,
    *,
    route: str = "asset.locate",
    acceptance_mode: ShadowAcceptanceMode = ShadowAcceptanceMode.INTERNAL_REAL,
) -> dict[str, Path]:
    runtime_dir = tmp_path / "runtime"
    investigation_dir = tmp_path / "investigation"
    composition_path = tmp_path / "pingan-enrichment.local.json"
    action_config_path = tmp_path / "pingan-actions.local.json"
    extensions_config_path = tmp_path / "pingan-extensions.local.json"
    adapter_id = (
        "asset-locate-pingan-mcp"
        if route == "asset.locate"
        else "asset-lookup-pingan-mcp"
    )
    result_mode = (
        "mock"
        if acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION
        else "real"
    )
    provider_mode = (
        "fake"
        if acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION
        else "internal"
    )
    composition = {
        "schema_version": "soc.enrichment_composition.v1",
        "enabled": True,
        "required_result_mode": result_mode,
        "policy": {
            "schema_version": "soc.enrichment_policy.v1",
            "policy_version": "pingan-shadow-test-v1",
            "tenant_id": "pingan",
            "enabled_routes": [route],
            "asset_route": route,
            "max_actions_total": 1,
            "max_actions_per_route": 1,
        },
        "retry_policy": {
            "max_attempts_per_action": 2,
            "stale_after_seconds": 300,
        },
        "bindings": [
            {
                "route": route,
                "action": route,
                "adapter_id": adapter_id,
                "adapter_kind": "mcp",
            }
        ],
    }
    action_config = {
        "adapters": [
            {
                "schema_version": "soc.mcp_action_adapter_config.v1",
                "enabled": True,
                "adapter_id": adapter_id,
                "route": route,
                "action": route,
                "risk_level": "read_only",
                "adapter_kind": "mcp",
                "external_side_effect": "read",
                "dry_run_supported": True,
                "execute_supported": True,
                "idempotency_required": False,
                "required_payload_fields": ["asset_key"],
                "required_context_refs": ["thread_id"],
                "metadata": {
                    "result_provenance_contract": "runtime_declared",
                    "result_mode_field": "mocked",
                },
                "mcp": {
                    "server": "pingan_asset",
                    "tool": "pingan_asset_asset_locate",
                    "output_fields": ["mocked"],
                    "result_schema_version": "soc.test_asset_result.v1",
                },
            }
        ]
    }
    extensions_config = {
        "mcpServers": {
            "pingan_asset": {
                "enabled": True,
                "type": "stdio",
                "command": "python",
                "args": ["server.py"],
                "env": {"SOC_PINGAN_ASSET_PROVIDER_MODE": provider_mode},
            }
        },
        "skills": {},
    }
    _write_json(composition_path, composition)
    _write_json(action_config_path, action_config)
    _write_json(extensions_config_path, extensions_config)
    composition_sha256 = _sha256_file(composition_path)
    action_sha256 = _sha256_file(action_config_path)
    extensions_sha256 = _sha256_file(extensions_config_path)

    source_sha256 = "a" * 64
    runtime_items: list[dict[str, object]] = []
    investigation_items: list[dict[str, object]] = []
    for index in range(5):
        row_sha256 = _sha256(f"row-{index}")
        payload_sha256 = _sha256(f"payload-{index}")
        runtime_item = _item(
            index=index,
            source_sha256=source_sha256,
            row_sha256=row_sha256,
            payload_sha256=payload_sha256,
            investigation=False,
            route=route,
            adapter_id=adapter_id,
            result_mode=result_mode,
        )
        investigation_item = _item(
            index=index,
            source_sha256=source_sha256,
            row_sha256=row_sha256,
            payload_sha256=payload_sha256,
            investigation=True,
            route=route,
            adapter_id=adapter_id,
            result_mode=result_mode,
        )
        runtime_items.append(runtime_item)
        investigation_items.append(investigation_item)

    _write_batch(
        runtime_dir,
        items=runtime_items,
        source_sha256=source_sha256,
        investigation=False,
        composition_sha256=None,
        action_config_sha256s=[],
        extensions_config_sha256=None,
    )
    _write_batch(
        investigation_dir,
        items=investigation_items,
        source_sha256=source_sha256,
        investigation=True,
        composition_sha256=composition_sha256,
        action_config_sha256s=[action_sha256],
        extensions_config_sha256=extensions_sha256,
    )
    return {
        "runtime_dir": runtime_dir,
        "investigation_dir": investigation_dir,
        "composition_path": composition_path,
        "action_config_path": action_config_path,
        "extensions_config_path": extensions_config_path,
    }


def _item(
    *,
    index: int,
    source_sha256: str,
    row_sha256: str,
    payload_sha256: str,
    investigation: bool,
    route: str,
    adapter_id: str,
    result_mode: str,
) -> dict[str, object]:
    analysis_run = {
        "normalization_report": {
            "adapter": "pingan_platform",
            "message_schemas": [
                {
                    "source_path": "alert.hitLog[0].zeusRawLogs[0].message",
                    "parser_name": "pingan_delimited_json",
                    "parser_version": "v2",
                    "schema_fingerprint": "b" * 64,
                    "status": "recognized",
                    "field_count": 20,
                    "warnings": [],
                }
            ],
        },
        "entities": {"ips": [f"10.0.0.{index + 1}"]},
        "extraction_report": {"mention_count": 1},
        "fact_reconstruction": {"role_resolutions": [], "warnings": []},
        "llm_analysis_request": {
            "alert_id": f"alert-{index}",
            "tenant_id": "pingan",
            "evidence_coverage": {"high_value_gaps": []},
        },
    }
    item: dict[str, object] = {
        "schema_version": "soc.pingan_internal_runtime_batch_item.v1",
        "outcome": "completed",
        "source": {
            "source_file_sha256": source_sha256,
            "source_index": index,
            "alert_id": f"alert-{index}",
            "row_sha256": row_sha256,
            "payload_sha256": payload_sha256,
        },
        "execution": {
            "analyzer_mode": "llm",
            "requested_model_name": "deepseek-v4-flash",
            "persisted": investigation,
            "investigation_enrichment_enabled": investigation,
            "duration_ms": 100 + index,
        },
        "summary": {
            "runtime_status": "needs_review",
            "verdict": "suspicious",
            "needs_review": True,
            "automation_allowed": False,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        },
        "analysis_run": analysis_run,
    }
    if investigation:
        item["investigation_workflow"] = {
            "execution": {"status": "completed"},
            "attempts": [
                {
                    "started_at": "2026-08-05T00:00:00+00:00",
                    "ended_at": f"2026-08-05T00:00:00.{index + 1:06d}+00:00",
                }
            ],
        }
        item["investigation_shadow_report"] = {
            "schema_version": "soc.investigation_shadow_report.v1",
            "execution_status": "completed",
            "required_result_mode": result_mode,
            "planned_action_count": 1,
            "attempt_count": 1,
            "provider_invocation_count": 1,
            "success_count": 1,
            "not_found_count": 0,
            "failed_count": 0,
            "retry_count": 0,
            "persisted_evidence_count": 1,
            "missing_evidence_count": 0,
            "routes": [
                {
                    "route": route,
                    "action": route,
                    "adapter_id": adapter_id,
                    "planned_action_count": 1,
                    "attempt_count": 1,
                    "provider_invocation_count": 1,
                    "success_count": 1,
                    "not_found_count": 0,
                    "final_failure_count": 0,
                    "provider_failure_attempt_count": 0,
                    "contract_failure_attempt_count": 0,
                    "denied_attempt_count": 0,
                    "interrupted_attempt_count": 0,
                    "persisted_evidence_count": 1,
                    "missing_evidence_count": 0,
                    "real_result_count": int(result_mode == "real"),
                    "mock_result_count": int(result_mode == "mock"),
                }
            ],
            "cost_measurement_status": "not_measured",
            "measurement_gaps": [
                "provider_cost_not_measured",
                "provider_network_latency_not_isolated_from_action_latency",
            ],
            "base_run_mutated": False,
            "auto_close_allowed": False,
            "confirmed_memory_write_allowed": False,
            "high_risk_actions_allowed": False,
        }
    return item


def _write_batch(
    path: Path,
    *,
    items: list[dict[str, object]],
    source_sha256: str,
    investigation: bool,
    composition_sha256: str | None,
    action_config_sha256s: list[str],
    extensions_config_sha256: str | None,
) -> None:
    items_dir = path / "items"
    items_dir.mkdir(parents=True)
    for item in items:
        index = item["source"]["source_index"]  # type: ignore[index]
        _write_json(items_dir / f"{index:07d}.json", item)
    manifest = {
        "schema_version": "soc.pingan_internal_runtime_batch_manifest.v1",
        "batch_id": f"batch-{'investigation' if investigation else 'runtime'}",
        "status": "completed",
        "source": {
            "sha256": source_sha256,
            "row_count": 100,
            "selected_count": len(items),
            "source_error_count": 0,
        },
        "execution": {
            "analyzer_mode": "llm",
            "model_name": "deepseek-v4-flash",
            "sensitive_evidence_mode": "full",
            "persist": investigation,
            "database_kind": "sqlite" if investigation else "none",
            "default_tenant_id": "pingan",
            "investigation_enrichment_enabled": investigation,
            "enrichment_composition_sha256": composition_sha256,
            "enrichment_action_config_sha256s": action_config_sha256s,
            "enrichment_extensions_config_sha256": extensions_config_sha256,
        },
        "summary": {
            "selected_count": len(items),
            "recorded_count": len(items),
            "pending_count": 0,
            "completed_count": len(items),
            "failed_count": 0,
            "source_error_count": 0,
        },
    }
    _write_json(path / "manifest.json", manifest)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
