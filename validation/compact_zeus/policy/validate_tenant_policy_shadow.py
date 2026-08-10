"""Validate PingAn tenant policy against saved real-model Runtime results."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.contracts import (  # noqa: E402
    AnalysisRun,
    TenantPolicyEvaluationStatus,
    TenantPolicyTimeSource,
)
from soc_agent.integrations.pingan.tenant_disposition import (  # noqa: E402
    load_pingan_tenant_disposition_policy,
)
from soc_agent.core.runtime import inspect_alert_normalization  # noqa: E402
from soc_agent.tenant_policy import evaluate_tenant_policy  # noqa: E402

DEFAULT_ARTIFACTS = (
    BACKEND_ROOT
    / ".deer-flow/soc-runtime-validation/checkpoint-d/step-d10-cross-source-runtime/runs/1965449.representative.runtime.json",
    BACKEND_ROOT
    / ".deer-flow/soc-runtime-validation/checkpoint-d/step-d10-cross-source-runtime/runs/1966442.representative.runtime.json",
)
DEFAULT_OUTPUT_DIR = (
    BACKEND_ROOT / ".deer-flow/soc-runtime-validation/tenant-policy-shadow"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the PingAn tenant policy without changing saved Runtime truth.",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        type=Path,
        default=[],
        help="Saved AnalysisRun JSON; repeat for multiple samples.",
    )
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--tenant-id", default="pingan")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    artifacts = tuple(args.artifact) or DEFAULT_ARTIFACTS
    missing = [path for path in artifacts if not path.is_file()]
    if missing:
        parser.error(
            "Runtime artifact not found: " + ", ".join(str(path) for path in missing)
        )

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    policy = load_pingan_tenant_disposition_policy()
    reports = [
        _evaluate_artifact(
            path,
            policy=policy,
            tenant_id=args.tenant_id,
            environment=args.environment,
        )
        for path in artifacts
    ]
    for report in reports:
        target = output_dir / f"{report['run']['alert_id']}.tenant-policy.json"
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    matched = [
        report
        for report in reports
        if report["tenant_policy_decision"]["evaluation_status"]
        == TenantPolicyEvaluationStatus.MATCHED.value
    ]
    summary = {
        "schema_version": "soc.tenant_policy_shadow_validation.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "policy": {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "tenant_id": policy.tenant_id,
            "environment": args.environment,
        },
        "sample_count": len(reports),
        "matched_count": len(matched),
        "no_match_count": len(reports) - len(matched),
        "detection_truth_unchanged_count": sum(
            report["invariants"]["detection_truth_unchanged"] for report in reports
        ),
        "runtime_object_unchanged_count": sum(
            report["invariants"]["runtime_object_unchanged"] for report in reports
        ),
        "all_shadow_only": all(
            report["invariants"]["shadow_only_no_operational_mutation"]
            for report in reports
        ),
        "samples": [
            {
                "alert_id": report["run"]["alert_id"],
                "run_id": report["run"]["run_id"],
                "runtime_verdict": report["runtime_before"]["verdict"],
                "policy_status": report["tenant_policy_decision"]["evaluation_status"],
                "selected_rule_id": report["tenant_policy_decision"][
                    "selected_rule_id"
                ],
                "response_posture": report["tenant_policy_decision"][
                    "response_posture"
                ],
                "policy_time_source": report["tenant_policy_decision"][
                    "policy_time_source"
                ],
            }
            for report in reports
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_shadow_only"] else 1


def _evaluate_artifact(
    path: Path,
    *,
    policy,
    tenant_id: str,
    environment: str,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    run_payload = payload.get("analysis_run", payload)
    run = AnalysisRun.model_validate(run_payload)
    if run.llm_analysis_request is None:
        raise ValueError(f"saved Runtime artifact has no llm_analysis_request: {path}")

    run.llm_analysis_request = run.llm_analysis_request.model_copy(
        update={"tenant_id": tenant_id},
    )
    before = run.model_dump(mode="json")
    runtime_truth = _runtime_truth(run)
    raw_policy_time = (
        inspect_alert_normalization(run.input_payload).alert.event.event_time
        if run.input_payload is not None
        else None
    )
    if raw_policy_time is not None and (
        raw_policy_time.tzinfo is None or raw_policy_time.utcoffset() is None
    ):
        policy_time = raw_policy_time.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        policy_time_source = TenantPolicyTimeSource.ALERT_EVENT_TIME_TIMEZONE_ASSUMED
    else:
        policy_time = raw_policy_time
        policy_time_source = (
            TenantPolicyTimeSource.ALERT_EVENT_TIME
            if policy_time is not None
            else TenantPolicyTimeSource.EVALUATION_TIME_FALLBACK
        )
    decision = evaluate_tenant_policy(
        policy,
        run,
        environment=environment,
        policy_time=policy_time,
        policy_time_source=policy_time_source,
    )
    after = run.model_dump(mode="json")
    policy_truth = decision.detection_truth.model_dump(mode="json")

    return {
        "schema_version": "soc.tenant_policy_shadow_sample.v1",
        "source": {
            "artifact": str(path.resolve()),
            "artifact_kind": "saved_real_model_runtime_result",
            "tenant_id_override": tenant_id,
            "tenant_override_scope": "validation_only",
        },
        "run": {
            "run_id": run.run_id,
            "alert_id": run.alert_id,
            "model_name": run.model_name,
            "prompt_version": run.prompt_version,
            "pipeline_version": run.pipeline_version,
        },
        "runtime_before": runtime_truth,
        "tenant_policy_decision": decision.model_dump(mode="json"),
        "invariants": {
            "detection_truth_unchanged": (
                runtime_truth["verdict"] == policy_truth["verdict"]
                and runtime_truth["confidence"] == policy_truth["confidence"]
            ),
            "runtime_object_unchanged": before == after,
            "shadow_only_no_operational_mutation": (
                decision.shadow_only
                and not decision.auto_apply_allowed
                and decision.detection_truth_impact == "none"
                and decision.review_queue_impact == "none"
                and decision.action_impact == "none"
                and decision.memory_impact == "none"
            ),
        },
    }


def _runtime_truth(run: AnalysisRun) -> dict[str, Any]:
    if run.decision is not None:
        return {
            "source": "decision",
            "verdict": run.decision.verdict.value,
            "confidence": run.decision.confidence,
            "suggested_action": run.decision.suggested_action,
            "needs_review": run.decision.needs_review,
        }
    if run.analysis is not None:
        return {
            "source": "analysis",
            "verdict": run.analysis.verdict.value,
            "confidence": run.analysis.confidence,
            "suggested_action": run.analysis.recommended_action,
            "needs_review": None,
        }
    raise ValueError(f"analysis run {run.run_id} has no detection truth")


if __name__ == "__main__":
    raise SystemExit(main())
