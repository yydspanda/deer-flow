#!/usr/bin/env python3
"""Build one live Checkpoint D-7 structured analyzer-output review artifact."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (  # noqa: E402
    canonical_sha256,
    write_json_atomic,
)

from soc_agent.contracts import LLMAnalysisRequest  # noqa: E402
from soc_agent.llm import (  # noqa: E402
    SocAnalyzerMode,
    SocLLMSettings,
    build_configured_analyzer,
)
from soc_agent.prompts import build_analysis_prompt  # noqa: E402
from soc_agent.protocols import LLMAnalyzer  # noqa: E402

SCHEMA_VERSION = "soc.validation.checkpoint_d.analyzer_output_review.v1"
DEFAULT_CHECKPOINT_D_ROOT = (
    ROOT / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
)
DEFAULT_ALERT_ID = 1965449
DEFAULT_MODEL_NAME = "globalai-deepseek-v4-flash-0731"
_ALLOWED_D5_STATUSES = {"passed", "passed_with_projection_notes"}


def build_analyzer_output_review(
    skill_context_review: Mapping[str, Any],
    *,
    alert_id: int,
    analyzer: LLMAnalyzer,
) -> dict[str, Any]:
    """Consume confirmed D5 output and invoke only the production analyzer boundary."""

    d5_acceptance = _required_mapping(skill_context_review, "acceptance", "D-5")
    d5_request_payload = _required_mapping(
        skill_context_review,
        "llm_analysis_request",
        "D-5",
    )
    request_hash_before = canonical_sha256(d5_request_payload)
    request = LLMAnalysisRequest.model_validate(d5_request_payload)
    prompt = build_analysis_prompt(request)
    node_output = analyzer.analyze(request)
    request_hash_after = canonical_sha256(
        request.model_dump(mode="json", exclude_none=True)
    )
    result_payload = node_output.analysis.model_dump(mode="json", exclude_none=True)
    assessments = node_output.analysis.scenario_assessments
    primary_assessments = [item for item in assessments if item.is_primary]
    evidence_refs = {item.evidence_ref for item in node_output.analysis.evidence}
    reasoning_refs = {item.reasoning_id for item in node_output.analysis.reasoning}
    selected_skills = [
        item.skill_name for item in request.skill_context.selected_skills
    ]

    checks = {
        "d5_acceptance_allows_continuation": d5_acceptance.get("status")
        in _ALLOWED_D5_STATUSES,
        "d5_alert_id_matches": str(
            _mapping_path(skill_context_review, "input", "alert_id")
        )
        == str(alert_id),
        "d5_request_unchanged": request_hash_before == request_hash_after,
        "live_llm_analyzer_used": node_output.model_name != "stub"
        and analyzer.step_name == "analyze_llm",
        "prompt_version_matches": node_output.prompt_version == prompt.prompt_version,
        "parser_version_recorded": bool(node_output.parser_version),
        "analysis_schema_is_v3": result_payload.get("schema_version")
        == "soc.analysis_result.v4",
        "evidence_is_non_empty": bool(node_output.analysis.evidence),
        "reasoning_is_non_empty": bool(node_output.analysis.reasoning),
        "scenario_assessment_is_non_empty": bool(assessments),
        "exactly_one_primary_scenario": len(primary_assessments) == 1,
        "scenario_evidence_references_are_valid": all(
            reference in evidence_refs
            for item in assessments
            for reference in item.evidence_refs
        ),
        "scenario_reasoning_references_are_valid": all(
            reference in reasoning_refs
            for item in assessments
            for reference in item.reasoning_refs
        ),
        "scenario_activity_stages_are_explicit": all(
            bool(item.activity_stage.value) for item in assessments
        ),
        "manual_checks_are_non_empty": bool(node_output.analysis.manual_checks),
        "d7_fields_are_serialized": {
            "scenario_assessments",
            "reasoning",
            "evidence_gaps",
            "manual_checks",
        }
        <= result_payload.keys(),
        "selected_skills_reach_prompt_context": selected_skills
        == [
            item["skill_name"]
            for item in prompt.context["skill_context"]["selected_skills"]
        ],
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    status = "failed" if failed_checks else "passed"

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "d5_contract_validation",
                "production_prompt_rendering",
                "configured_live_llm_analyzer_call",
                "json_parse_and_conservative_repair",
                "analysis_result_v3_schema_validation",
                "explicit_fact_and_reasoning_reference_validation",
                "typed_scenario_assessment_validation",
            ],
            "not_performed": [
                "evidence_grounding",
                "decision_policy",
                "correlation_or_memory_retrieval",
                "tool_or_mcp_invocation",
                "persistence",
                "review_queue_or_action",
            ],
        },
        "input": {
            "alert_id": alert_id,
            "topic": _mapping_path(skill_context_review, "input", "topic"),
            "d5_schema_version": skill_context_review.get("schema_version"),
            "d5_status": d5_acceptance.get("status"),
            "d5_request_sha256": request_hash_before,
            "selected_skills": selected_skills,
        },
        "acceptance": {
            "status": status,
            "failed_checks": failed_checks,
            "checks": checks,
            "evidence_count": len(node_output.analysis.evidence),
            "reasoning_count": len(node_output.analysis.reasoning),
            "scenario_assessment_count": len(assessments),
            "evidence_gap_count": len(node_output.analysis.evidence_gaps),
            "manual_check_count": len(node_output.analysis.manual_checks),
        },
        "prompt_contract": {
            "prompt_version": prompt.prompt_version,
            "context_sha256": canonical_sha256(prompt.context),
            "response_schema": prompt.response_schema,
        },
        "analyzer": {
            "step_name": analyzer.step_name,
            "model_name": node_output.model_name,
            "prompt_version": node_output.prompt_version,
            "parser_version": node_output.parser_version,
            "metadata": _bounded_analyzer_metadata(node_output.metadata),
        },
        "scenario_review": {
            "upstream_hypotheses": [
                item.model_dump(mode="json", exclude_none=True)
                for item in request.fact_reconstruction.scenario_hypotheses
            ],
            "primary_scenario": (
                primary_assessments[0].model_dump(mode="json", exclude_none=True)
                if len(primary_assessments) == 1
                else None
            ),
            "all_scenarios": [
                item.model_dump(mode="json", exclude_none=True) for item in assessments
            ],
        },
        "analysis_result": result_payload,
        "analysis_result_sha256": canonical_sha256(result_payload),
    }


def _bounded_analyzer_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "analyzer",
        "candidate_hash",
        "prompt_hash",
        "provider_call_count",
        "provider_calls",
        "provider_call_measured_duration_ms",
        "repair_applied",
        "repair_log",
        "response_metadata",
        "selected_skills",
        "skill_context_hash",
        "usage",
        "usage_measurement",
    }
    return {key: value for key, value in metadata.items() if key in allowed}


def _required_mapping(
    value: Mapping[str, Any],
    key: str,
    artifact_name: str,
) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"{artifact_name} artifact is missing {key}")
    return item


def _mapping_path(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alert-id", type=int, default=DEFAULT_ALERT_ID)
    parser.add_argument("--skill-context-review", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--analyzer-mode",
        choices=[item.value for item in SocAnalyzerMode],
        default=SocAnalyzerMode.LLM.value,
    )
    parser.add_argument(
        "--model-name",
        default=os.environ.get("SOC_VALIDATION_MODEL", DEFAULT_MODEL_NAME),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    skill_context_review_path = args.skill_context_review or (
        DEFAULT_CHECKPOINT_D_ROOT
        / "step-d5-skill-context"
        / f"{args.alert_id}.skill-context.json"
    )
    output_dir = args.output_dir or (
        DEFAULT_CHECKPOINT_D_ROOT / "step-d7-analyzer-output"
    )
    skill_context_review = json.loads(
        skill_context_review_path.read_text(encoding="utf-8")
    )
    settings = SocLLMSettings.from_env().with_overrides(
        mode=args.analyzer_mode,
        model_name=args.model_name,
    )
    analyzer = build_configured_analyzer(settings=settings)
    review = build_analyzer_output_review(
        skill_context_review,
        alert_id=args.alert_id,
        analyzer=analyzer,
    )
    output_path = output_dir / f"{args.alert_id}.analyzer-output.json"
    write_json_atomic(review, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "alert_id": args.alert_id,
                "status": review["acceptance"]["status"],
                "failed_checks": review["acceptance"]["failed_checks"],
                "model_name": review["analyzer"]["model_name"],
                "primary_scenario": _mapping_path(
                    review,
                    "scenario_review",
                    "primary_scenario",
                    "scenario_name",
                ),
                "activity_stage": _mapping_path(
                    review,
                    "scenario_review",
                    "primary_scenario",
                    "activity_stage",
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if review["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
