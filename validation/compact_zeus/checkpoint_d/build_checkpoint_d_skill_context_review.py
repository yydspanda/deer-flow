#!/usr/bin/env python3
"""Build one Checkpoint D-5 bounded SOC skill-context review artifact."""

from __future__ import annotations

import argparse
import json
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
from soc_agent.pipeline.analysis_context import (  # noqa: E402
    resolve_skill_context_for_request,
)
from soc_agent.skills import (  # noqa: E402
    SOC_ALERT_TRIAGE_SKILL,
    SocSkillResolver,
)

SCHEMA_VERSION = "soc.validation.checkpoint_d.skill_context_review.v1"
DEFAULT_CHECKPOINT_D_ROOT = ROOT / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
DEFAULT_ALERT_ID = 1965449
_ALLOWED_D4_STATUSES = {"passed", "passed_with_coverage_findings"}


def build_skill_context_review(
    analysis_input_review: Mapping[str, Any],
    *,
    alert_id: int,
) -> dict[str, Any]:
    """Consume confirmed D4 output and invoke the production D5 boundary only."""

    d4_acceptance = _required_mapping(analysis_input_review, "acceptance", "D-4")
    d4_request_payload = _required_mapping(
        analysis_input_review,
        "llm_analysis_request",
        "D-4",
    )
    d4_hash_before = canonical_sha256(d4_request_payload)
    request = LLMAnalysisRequest.model_validate(d4_request_payload)
    resolution = SocSkillResolver().resolve_for_analysis_request(request)
    skill_context = resolve_skill_context_for_request(request)
    enriched_request = request.model_copy(update={"skill_context": skill_context})
    d4_hash_after = canonical_sha256(d4_request_payload)

    request_before = request.model_dump(mode="json", exclude_none=True)
    request_after = enriched_request.model_dump(mode="json", exclude_none=True)
    before_without_context = dict(request_before)
    after_without_context = dict(request_after)
    before_without_context.pop("skill_context", None)
    after_without_context.pop("skill_context", None)

    selected_resolution_names = [item.skill_name for item in resolution.selected_skills]
    selected_context_names = [item.skill_name for item in skill_context.selected_skills]
    rejected_skills = [
        {
            "skill_name": skill_name,
            "reason": "no deterministic routing trigger matched this D4 request",
        }
        for skill_name in resolution.available_agent_skills
        if skill_name not in selected_resolution_names
    ]
    package_items = skill_context.selected_skills
    checks = {
        "d4_acceptance_allows_continuation": d4_acceptance.get("status") in _ALLOWED_D4_STATUSES,
        "d4_alert_id_matches": str(_mapping_path(analysis_input_review, "input", "alert_id")) == str(alert_id),
        "d4_skill_context_is_empty": _mapping_path(
            d4_request_payload,
            "skill_context",
            "selected_skills",
        )
        == [],
        "d4_payload_unchanged": d4_hash_before == d4_hash_after,
        "only_skill_context_changed": canonical_sha256(before_without_context) == canonical_sha256(after_without_context),
        "baseline_skill_selected": SOC_ALERT_TRIAGE_SKILL in selected_resolution_names,
        "resolution_and_projection_match": selected_resolution_names == selected_context_names,
        "all_selected_packages_projected": len(package_items) == len(resolution.selected_skills),
        "guidance_comes_from_skill_packages": all(item.guidance_source in {"references/runtime-guidance.md", "SKILL.md#description"} for item in package_items),
        "guidance_is_within_per_skill_budget": all(item.estimated_token_count <= item.token_budget for item in package_items),
        "guidance_hashes_are_present": all(len(item.guidance_hash) == 64 and len(item.package_hash) == 64 for item in package_items),
        "total_budget_matches_items": skill_context.total_token_budget == sum(item.token_budget for item in package_items),
        "estimated_token_count_matches_items": (skill_context.total_estimated_token_count == sum(item.estimated_token_count for item in package_items)),
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    if failed_checks:
        status = "failed"
    elif skill_context.notes:
        status = "passed_with_projection_notes"
    else:
        status = "passed"

    resolution_payload = resolution.model_dump(mode="json", exclude_none=True)
    context_payload = skill_context.model_dump(mode="json", exclude_none=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "d4_contract_validation",
                "production_soc_skill_resolution",
                "deerflow_public_skill_package_validation",
                "bounded_runtime_guidance_projection",
                "skill_package_and_projection_hashing",
                "token_budget_validation",
            ],
            "not_performed": [
                "prompt_rendering",
                "analyzer_or_llm",
                "evidence_grounding",
                "decision_policy",
                "persistence",
            ],
        },
        "input": {
            "alert_id": alert_id,
            "topic": _mapping_path(analysis_input_review, "input", "topic"),
            "d4_schema_version": analysis_input_review.get("schema_version"),
            "d4_status": d4_acceptance.get("status"),
            "d4_request_sha256": d4_hash_before,
        },
        "acceptance": {
            "status": status,
            "failed_checks": failed_checks,
            "checks": checks,
            "selected_skill_count": len(selected_context_names),
            "rejected_skill_count": len(rejected_skills),
            "total_estimated_token_count": skill_context.total_estimated_token_count,
            "total_token_budget": skill_context.total_token_budget,
        },
        "selection_features": _selection_features(request),
        "resolution": resolution_payload,
        "rejected_skills": rejected_skills,
        "skill_context": context_payload,
        "skill_context_sha256": canonical_sha256(context_payload),
        "llm_analysis_request": request_after,
    }


def _selection_features(request: LLMAnalysisRequest) -> dict[str, Any]:
    http = request.canonical_entities.http
    email = request.canonical_entities.email
    entities = request.extracted_entities
    return {
        "source_type": request.source.source_type.value,
        "source_system": request.source.source_system,
        "detection": {
            "rule_code": request.detection.rule_code,
            "rule_name": request.detection.rule_name,
            "rule_category": request.detection.rule_category,
        },
        "typed_evidence": {
            "http": bool(http.observations or http.method or http.host or http.path or http.url or http.status_code is not None),
            "email": bool(email and (email.observations or email.message_id or email.sender_addresses or email.recipient_addresses or email.subject or email.links or email.attachment_names)),
        },
        "extracted_entity_counts": {
            "ips": len(entities.ips),
            "domains": len(entities.domains),
            "urls": len(entities.urls),
            "emails": len(entities.emails),
            "processes": len(entities.processes),
            "users": len(entities.users),
            "hosts": len(entities.hosts),
            "assets": len(entities.assets),
        },
        "role_resolutions": [
            {
                "role": item.role,
                "status": item.status.value,
                "selected_value_present": item.selected_value is not None,
            }
            for item in request.fact_reconstruction.role_resolutions
        ],
        "scenario_types": [item.scenario_type for item in request.fact_reconstruction.scenario_hypotheses],
        "conflict_count": request.conflict_count,
        "conflict_types": list(request.conflict_types),
        "high_value_gap_count": len(request.evidence_coverage.high_value_gaps),
    }


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
    parser.add_argument("--analysis-input-review", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    analysis_input_review_path = args.analysis_input_review or (DEFAULT_CHECKPOINT_D_ROOT / "step-d4-bounded-analysis-input" / f"{args.alert_id}.analysis-input.json")
    output_dir = args.output_dir or (DEFAULT_CHECKPOINT_D_ROOT / "step-d5-skill-context")
    analysis_input_review = json.loads(analysis_input_review_path.read_text(encoding="utf-8"))
    review = build_skill_context_review(
        analysis_input_review,
        alert_id=args.alert_id,
    )
    output_path = output_dir / f"{args.alert_id}.skill-context.json"
    write_json_atomic(review, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "alert_id": args.alert_id,
                "status": review["acceptance"]["status"],
                "failed_checks": review["acceptance"]["failed_checks"],
                "selected_skills": [item["skill_name"] for item in review["skill_context"]["selected_skills"]],
                "total_estimated_token_count": review["acceptance"]["total_estimated_token_count"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if review["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
