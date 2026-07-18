"""Build manifests and a review index for the local SOC Runtime validation run."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "backend/.deer-flow/soc-runtime-validation"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    generate(args.root)
    return 0


def generate(root: Path) -> None:
    generated_at = datetime.now(UTC).isoformat()
    _write_evaluation_manifests(root)
    tracks: list[dict[str, Any]] = []
    tracks.extend(_existing_core_tracks(root))
    tracks.extend(_live_tracks(root))
    tracks.extend(_evaluation_tracks(root))

    passed = sum(item["status"] == "passed" for item in tracks)
    expected_pending = sum(item["status"] == "expected_pending" for item in tracks)
    failed = sum(item["status"] in {"failed", "missing"} for item in tracks)
    index = {
        "schema_version": "soc.runtime_validation.index.v2",
        "generated_at": generated_at,
        "storage_policy": {
            "git_ignored": True,
            "contains_real_alert_derived_data": True,
            "commit_allowed": False,
        },
        "fixed_runtime_pipeline": [
            "normalize",
            "entity_extract",
            "fact_reconstruct",
            "build_analysis_input",
            "skill_context",
            "analyze",
            "schema_validate",
            "evidence_grounding",
            "decide",
        ],
        "classification_note": ("Steps 1-6 and 8 validate the bounded Runtime path; Step 7 is an offline normalization-maintenance sidecar; Steps 9-12 validate labeling, correlation, and governed-context boundaries."),
        "summary": {
            "track_count": len(tracks),
            "passed_count": passed,
            "expected_pending_count": expected_pending,
            "failed_or_missing_count": failed,
            "overall_status": "passed_with_expected_human_boundary" if failed == 0 else "incomplete",
        },
        "review_findings": _review_findings(root),
        "tracks": tracks,
    }
    _write_json(root / "manifest.json", index)
    _write_json(root / "latest-run.json", index)
    (root / "RUN-INDEX.md").write_text(_render_markdown(index), encoding="utf-8")


def _write_evaluation_manifests(root: Path) -> None:
    replay_dir = root / "step-10-five-sample-repair"
    replay_runs = sorted(replay_dir.glob("*.deterministic.json"))
    replay_values = [_load_optional(path) for path in replay_runs]
    replay_passed = len(replay_runs) == 5 and all(value and value.get("runtime_failure") is None for value in replay_values)
    _write_json(
        replay_dir / "manifest.json",
        {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "step": {"number": 10, "name": "five_sample_deterministic_replay"},
            "artifact_count": len(replay_runs),
            "status": "passed" if replay_passed else "failed",
            "entries": [path.name for path in replay_runs],
            "live_comparison_source": "../step-09-confidence-labeling/runs",
            "git_ignored": True,
        },
    )

    bridge_dir = root / "step-10-correlation-bridge"
    bridge = _load_optional(bridge_dir / "pingan-main.json")
    bridge_passed = bool(bridge and bridge.get("failed_count") == 0 and bridge.get("sample_count") == 3)
    _write_json(
        bridge_dir / "manifest.json",
        {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "step": {"number": 10, "name": "correlation_bridge"},
            "artifact_count": 1 if bridge else 0,
            "status": "passed" if bridge_passed else "failed",
            "sample_count": (bridge or {}).get("sample_count", 0),
            "correlation_match_count": (bridge or {}).get("correlation_match_count", 0),
            "reusable_evidence_count": (bridge or {}).get("reusable_evidence_count", 0),
            "domain_finding_count": (bridge or {}).get("domain_finding_count", 0),
            "git_ignored": True,
        },
    )

    correlation_dir = root / "step-11-correlation-eval"
    baseline = _load_optional(correlation_dir / "correlation-baseline.json")
    replay = _load_optional(correlation_dir / "correlation-replay-diff.json")
    correlation_passed = bool(baseline and replay and baseline.get("integrity_passed") and replay.get("integrity_passed") and baseline.get("shadow_dedup_allowed") is False and replay.get("shadow_dedup_allowed") is False)
    _write_json(
        correlation_dir / "manifest.json",
        {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "step": {"number": 11, "name": "correlation_evaluation"},
            "artifact_count": int(baseline is not None) + int(replay is not None),
            "status": "passed" if correlation_passed else "failed",
            "fixture_set_id": (baseline or {}).get("fixture_set_id"),
            "pair_count": (baseline or {}).get("pair_count", 0),
            "retrieval_metrics": (baseline or {}).get("retrieval_metrics", {}),
            "dedup_metrics": (baseline or {}).get("dedup_metrics", {}),
            "replay_diff": (replay or {}).get("diff", {}),
            "shadow_dedup_allowed": False,
            "boundary": "Controlled corpus only; threshold is not a production suppression rule.",
            "git_ignored": True,
        },
    )


def _existing_core_tracks(root: Path) -> list[dict[str, Any]]:
    specs = [
        (1, "input_adapter_detection", "step-01-input-adapter", "Runtime input preparation / 输入适配"),
        (2, "normalization_and_message_parsing", "step-02-message-parsing", "Canonical normalization / 规范化与 message 解析"),
        (3, "fact_reconstruction", "step-03-fact-reconstruction", "Fact reconstruction / 事实与角色重建"),
        (4, "build_analysis_input", "step-04-build-analysis-input", "Bounded analysis input / 有界模型输入"),
        (5, "normalization_maintenance", "step-05-normalization-maintenance", "Schema drift maintenance / 解析漂移维护"),
    ]
    tracks = []
    for number, name, directory, purpose in specs:
        manifest = _load_optional(root / directory / "manifest.json")
        tracks.append(
            {
                "track_id": directory,
                "sequence": number,
                "classification": "runtime_preparation" if number < 5 else "maintenance_sidecar",
                "name": name,
                "purpose": purpose,
                "command": "./scripts/soc-runtime-validation.sh core",
                "manifest": f"{directory}/manifest.json",
                "artifact_count": (manifest or {}).get("artifact_count", 0),
                "status": "passed" if manifest else "missing",
            }
        )
    return tracks


def _live_tracks(root: Path) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    run_path = root / "step-06-live-llm/apt-1965449.step-06.json"
    run = _load_optional(run_path)
    if run:
        analyzer_step = _find_step(run, "analyze_llm")
        usage = (analyzer_step or {}).get("metadata", {}).get("usage", {})
        decision = run.get("decision") or {}
        grounding = run.get("analysis_evidence_grounding") or {}
        manifest = {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "step": {"number": 6, "name": "live_llm_analysis", "output_contract": "soc.analysis_run.v1"},
            "artifact_count": 1,
            "git_ignored": True,
            "model_name": run.get("model_name"),
            "prompt_version": run.get("prompt_version"),
            "run_id": run.get("run_id"),
            "runtime_status": run.get("status"),
            "status": "passed" if run.get("runtime_failure") is None else "failed",
            "duration_ms": (analyzer_step or {}).get("duration_ms"),
            "usage": usage,
            "decision": {
                "verdict": decision.get("verdict"),
                "needs_review": decision.get("needs_review"),
                "evidence_state": decision.get("evidence_state"),
                "review_reasons": decision.get("review_reasons", []),
            },
            "grounding": {
                "total_count": grounding.get("total_count", 0),
                "grounded_count": grounding.get("grounded_count", 0),
                "ungrounded_count": grounding.get("ungrounded_count", 0),
            },
            "entries": [{"source": "datas/apt-1965449.json", "artifact": run_path.name}],
        }
        _write_json(root / "step-06-live-llm/manifest.json", manifest)
        live_status = manifest["status"]
    else:
        live_status = "missing"
    tracks.append(
        {
            "track_id": "step-06-live-llm",
            "sequence": 6,
            "classification": "bounded_runtime",
            "name": "live_llm_analysis",
            "purpose": "Live bounded reasoning / 真实模型受控推理",
            "command": "./scripts/soc-runtime-validation.sh live",
            "manifest": "step-06-live-llm/manifest.json",
            "artifact_count": 1 if run else 0,
            "status": live_status,
        }
    )

    suggestion_path = root / "step-07-live-normalization-suggestion/apt-1965449.step-07.json"
    suggestion = _load_optional(suggestion_path)
    if suggestion:
        manifest = {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "step": {"number": 7, "name": "live_normalization_suggestion"},
            "classification": "offline_maintenance_sidecar",
            "artifact_count": 1,
            "status": "passed" if suggestion.get("auto_apply_allowed") is False else "failed",
            "model_name": suggestion.get("model_name"),
            "prompt_version": suggestion.get("prompt_version"),
            "suggestion_count": len(suggestion.get("suggestions", [])),
            "auto_apply_allowed": suggestion.get("auto_apply_allowed"),
            "warnings": suggestion.get("warnings", []),
            "usage": suggestion.get("usage", {}),
            "git_ignored": True,
        }
        _write_json(root / "step-07-live-normalization-suggestion/manifest.json", manifest)
        suggestion_status = manifest["status"]
    else:
        suggestion_status = "missing"
    tracks.append(
        {
            "track_id": "step-07-live-normalization-suggestion",
            "sequence": 7,
            "classification": "offline_maintenance_sidecar",
            "name": "live_normalization_suggestion",
            "purpose": "LLM mapping candidates / 仅离线生成字段映射候选",
            "command": "./scripts/soc-runtime-validation.sh live",
            "manifest": "step-07-live-normalization-suggestion/manifest.json",
            "artifact_count": 1 if suggestion else 0,
            "status": suggestion_status,
        }
    )

    hardening_status = _write_hardening_artifact(root, run)
    tracks.append(
        {
            "track_id": "step-08-runtime-hardening",
            "sequence": 8,
            "classification": "bounded_runtime",
            "name": "runtime_hardening",
            "purpose": "Grounding and decision-policy gates / 证据落地与决策门禁",
            "command": "./scripts/soc-runtime-validation.sh live",
            "manifest": "step-08-runtime-hardening/manifest.json",
            "artifact_count": 1 if run else 0,
            "status": hardening_status,
        }
    )
    return tracks


def _write_hardening_artifact(root: Path, run: dict[str, Any] | None) -> str:
    if not run:
        return "missing"
    grounding = run.get("analysis_evidence_grounding") or {}
    decision = run.get("decision") or {}
    steps = {item.get("step_name"): item for item in run.get("steps", [])}
    ungrounded_count = grounding.get("ungrounded_count", 0)
    review_reasons = decision.get("review_reasons", [])
    assertions = {
        "runtime_completed_without_failure": run.get("runtime_failure") is None,
        "schema_validation_succeeded": (steps.get("schema_validate") or {}).get("status") == "success",
        "evidence_grounding_executed": (steps.get("evidence_grounding") or {}).get("status") == "success",
        "ungrounded_evidence_forces_safe_review": (
            ungrounded_count == 0 or (decision.get("evidence_state") == "degraded" and "ungrounded_analysis_evidence" in review_reasons and decision.get("needs_review") is True and decision.get("automation_allowed") is False)
        ),
        "decision_policy_versioned": bool(decision.get("policy_version")),
        "confidence_not_misrepresented_as_calibrated": decision.get("confidence_is_calibrated") is False,
        "human_review_required": decision.get("needs_review") is True,
        "automation_blocked": decision.get("automation_allowed") is False,
    }
    status = "passed" if all(assertions.values()) else "failed"
    directory = root / "step-08-runtime-hardening"
    directory.mkdir(parents=True, exist_ok=True)
    artifact = {
        "schema_version": "soc.runtime_validation.step08.v1",
        "step": {"number": 8, "name": "runtime_hardening"},
        "source_run": "../step-06-live-llm/apt-1965449.step-06.json",
        "run_id": run.get("run_id"),
        "status": status,
        "analysis_quality_status": "clean" if ungrounded_count == 0 else "degraded",
        "assertions": assertions,
        "grounding": {
            "total_count": grounding.get("total_count", 0),
            "grounded_count": grounding.get("grounded_count", 0),
            "ungrounded_count": grounding.get("ungrounded_count", 0),
            "warnings": grounding.get("warnings", []),
        },
        "decision_boundary": {
            "verdict": decision.get("verdict"),
            "evidence_state": decision.get("evidence_state"),
            "review_reasons": decision.get("review_reasons", []),
            "automation_allowed": decision.get("automation_allowed"),
        },
    }
    _write_json(directory / "apt-1965449.step-08.json", artifact)
    _write_json(
        directory / "manifest.json",
        {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "step": {"number": 8, "name": "runtime_hardening"},
            "artifact_count": 1,
            "status": status,
            "analysis_quality_status": "clean" if ungrounded_count == 0 else "degraded",
            "ungrounded_evidence_count": ungrounded_count,
            "assertions": assertions,
            "git_ignored": True,
        },
    )
    return status


def _evaluation_tracks(root: Path) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    label_validation = _load_optional(root / "step-09-confidence-labeling/validation.rerun.pending.json")
    live_runs = list((root / "step-09-confidence-labeling/runs").glob("*.live.json"))
    live_results = [_live_result(path) for path in sorted(live_runs)]
    label_status = "expected_pending" if label_validation and not label_validation.get("calibratable") else ("passed" if label_validation else "missing")
    _write_json(
        root / "step-09-confidence-labeling/manifest.json",
        {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "step": {"number": 9, "name": "confidence_labeling_boundary"},
            "artifact_count": len(live_runs) + (2 if label_validation else 0),
            "live_run_count": len(live_runs),
            "status": label_status,
            "calibratable": (label_validation or {}).get("calibratable"),
            "pending_count": (label_validation or {}).get("pending_count"),
            "results": live_results,
            "grounding_summary": {
                "grounded_count": sum(item["grounded_count"] for item in live_results),
                "ungrounded_count": sum(item["ungrounded_count"] for item in live_results),
                "degraded_sample_count": sum(item["ungrounded_count"] > 0 for item in live_results),
            },
            "boundary": "Human truth review is required; the existing analyst-reviewed label set is never overwritten.",
            "git_ignored": True,
        },
    )
    tracks.append(
        {
            "track_id": "step-09-confidence-labeling",
            "sequence": 9,
            "classification": "offline_evaluation",
            "name": "confidence_labeling_boundary",
            "purpose": "Human-label gate / 人工真值与置信度校准边界",
            "command": "./scripts/soc-runtime-validation.sh live",
            "manifest": "step-09-confidence-labeling/manifest.json",
            "artifact_count": len(live_runs) + (2 if label_validation else 0),
            "status": label_status,
        }
    )

    tracks.append(_manifest_track(root, 10, "step-10-five-sample-repair", "deterministic_replay", "Five-sample deterministic replay / 五样本确定性回放"))
    tracks.append(_manifest_track(root, 10, "step-10-correlation-bridge", "correlation_bridge", "Main orchestrator correlation bridge / 主编排关联桥"))
    tracks.append(_manifest_track(root, 11, "step-11-correlation-eval", "correlation_evaluation", "Correlation retrieval evaluation / 关联检索离线评测"))
    tracks.append(_manifest_track(root, 11, "step-11-governed-context", "governed_context_lifecycle", "Governed fact lifecycle / 治理事实生命周期"))
    tracks.append(_manifest_track(root, 12, "step-12-authorization-shadow", "authorization_shadow", "Authorization read-only shadow match / 授权事实只读影子匹配"))
    return tracks


def _manifest_track(root: Path, sequence: int, directory: str, name: str, purpose: str) -> dict[str, Any]:
    manifest = _load_optional(root / directory / "manifest.json")
    return {
        "track_id": directory,
        "sequence": sequence,
        "classification": "offline_evaluation" if "correlation" in directory or "repair" in directory else "governance_sidecar",
        "name": name,
        "purpose": purpose,
        "command": "./scripts/soc-runtime-validation.sh evaluations",
        "manifest": f"{directory}/manifest.json",
        "artifact_count": (manifest or {}).get("artifact_count", 0),
        "status": (manifest or {}).get("status", "missing"),
    }


def _find_step(run: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((item for item in run.get("steps", []) if item.get("step_name") == name), None)


def _live_result(path: Path) -> dict[str, Any]:
    run = _load_optional(path) or {}
    grounding = run.get("analysis_evidence_grounding") or {}
    decision = run.get("decision") or {}
    analyzer = _find_step(run, "analyze_llm") or {}
    return {
        "sample": path.stem.removesuffix(".live"),
        "artifact": f"runs/{path.name}",
        "run_id": run.get("run_id"),
        "alert_id": run.get("alert_id"),
        "runtime_status": run.get("status"),
        "verdict": decision.get("verdict"),
        "confidence": decision.get("confidence"),
        "needs_review": decision.get("needs_review"),
        "grounded_count": grounding.get("grounded_count", 0),
        "ungrounded_count": grounding.get("ungrounded_count", 0),
        "duration_ms": analyzer.get("duration_ms"),
        "total_tokens": analyzer.get("metadata", {}).get("usage", {}).get("total_tokens"),
    }


def _review_findings(root: Path) -> list[dict[str, Any]]:
    runs = sorted((root / "step-09-confidence-labeling/runs").glob("*.live.json"))
    live_results = [_live_result(path) for path in runs]
    degraded = [item for item in live_results if item["ungrounded_count"] > 0]
    findings: list[dict[str, Any]] = []
    if degraded:
        findings.append(
            {
                "finding_id": "live_evidence_grounding_degraded",
                "severity": "review_required",
                "summary": f"{len(degraded)} of {len(live_results)} live samples contain rejected analyzer evidence references.",
                "samples": [
                    {
                        "sample": item["sample"],
                        "ungrounded_count": item["ungrounded_count"],
                    }
                    for item in degraded
                ],
                "safety_result": "Decision policy forced degraded evidence state, human review, and automation_allowed=false.",
            }
        )
    label_validation = _load_optional(root / "step-09-confidence-labeling/validation.rerun.pending.json")
    if label_validation and not label_validation.get("calibratable"):
        findings.append(
            {
                "finding_id": "confidence_labels_require_human_truth",
                "severity": "expected_boundary",
                "summary": f"{label_validation.get('pending_count', 0)} labels remain pending analyst review; calibration is intentionally blocked.",
            }
        )
    return findings


def _load_optional(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _render_markdown(index: dict[str, Any]) -> str:
    summary = index["summary"]
    lines = [
        "# SOC Runtime Validation - Latest Run / 最新验证索引",
        "",
        f"> Generated / 生成时间: `{index['generated_at']}`  ",
        "> Local derived artifacts only; do not commit / 仅本地衍生产物，禁止提交。",
        "",
        "## Result / 结果",
        "",
        f"- Overall / 总状态: **{summary['overall_status']}**",
        f"- Passed / 通过: **{summary['passed_count']}**",
        f"- Expected human boundary / 预期人工边界: **{summary['expected_pending_count']}**",
        f"- Failed or missing / 失败或缺失: **{summary['failed_or_missing_count']}**",
        "",
        "## Runtime Pipeline / 固定运行时流水线",
        "",
        "```text",
        " -> ".join(index["fixed_runtime_pipeline"]),
        "```",
        "",
        "> Step 7 is an offline maintenance sidecar. Steps 9-12 are evaluation/governance tracks, not hidden Runtime nodes.  ",
        "> Step 7 是离线维护旁路；Step 9-12 是评测/治理轨道，不是 Runtime 中暗藏的固定节点。",
        "",
        "## Artifacts / 产物",
        "",
        "| Seq | Track | Purpose / 作用 | Status | Artifact |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for track in index["tracks"]:
        lines.append(f"| {track['sequence']} | `{track['track_id']}` | {track['purpose']} | `{track['status']}` | `{track['manifest']}` |")
    lines.extend(
        [
            "",
            "## Findings / 本次发现",
            "",
        ]
    )
    if index["review_findings"]:
        for finding in index["review_findings"]:
            lines.append(f"- `{finding['finding_id']}` ({finding['severity']}): {finding['summary']}")
    else:
        lines.append("- No review findings / 无待审阅发现。")
    lines.extend(
        [
            "",
            "## Re-run / 重跑命令",
            "",
            "```bash",
            "./scripts/soc-runtime-validation.sh core",
            "./scripts/soc-runtime-validation.sh live",
            "./scripts/soc-runtime-validation.sh evaluations",
            "./scripts/soc-runtime-validation.sh finalize",
            "# or / 或一次执行",
            "./scripts/soc-runtime-validation.sh all",
            "```",
            "",
            "`live` calls the configured model and therefore needs valid model credentials. Confidence labels intentionally remain pending until an analyst supplies truth.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
