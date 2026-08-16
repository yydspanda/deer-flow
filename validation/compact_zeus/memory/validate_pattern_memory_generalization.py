#!/usr/bin/env python3
"""Validate exact Pattern Memory generalization across volatile IP changes."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.application import build_soc_memory_profile_registry  # noqa: E402
from soc_agent.contracts import (  # noqa: E402
    AnalysisContextReferenceKind,
    AnalysisRun,
    LLMAnalysisRequest,
    SocMemoryApplicabilityStatus,
    SocMemoryRecord,
)
from soc_agent.core import SocMemoryService  # noqa: E402
from soc_agent.memory import (  # noqa: E402
    ConfirmedMemoryAnalysisRequestEnricher,
    InMemoryMemoryCandidateRepository,
    memory_query_from_analysis_request,
)

REPORT_SCHEMA_VERSION = "soc.validation.pattern_memory_generalization.v1"
SOURCE_IP_REPLACEMENT = "30.116.114.151"
DESTINATION_IP_REPLACEMENT = "30.174.29.45"


def load_analysis_run(path: Path) -> AnalysisRun:
    payload = json.loads(path.read_text(encoding="utf-8"))
    run_payload = payload.get("analysis_run") if isinstance(payload, Mapping) else None
    return AnalysisRun.model_validate(
        run_payload if isinstance(run_payload, Mapping) else payload
    )


def load_confirmed_memory(path: Path) -> SocMemoryRecord:
    return SocMemoryRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))


def validate_pattern_memory_generalization(
    base_run: AnalysisRun,
    memory: SocMemoryRecord,
    *,
    output_dir: Path,
) -> dict[str, Any]:
    """Run positive cross-IP and negative semantic-control retrieval cases."""

    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"validation output directory is not empty: {output_dir}")
    if base_run.llm_analysis_request is None:
        raise ValueError("base AnalysisRun requires llm_analysis_request")
    if memory.applicability is None or memory.decision_directive is None:
        raise ValueError(
            "confirmed Pattern Memory requires applicability and directive"
        )

    output_dir.mkdir(parents=True, mode=0o700)
    output_dir.chmod(0o700)

    base_request = _apply_governed_scope(base_run.llm_analysis_request, memory)
    source_ip, destination_ip = _network_endpoints(base_request)
    repository = InMemoryMemoryCandidateRepository()
    repository.save_memory_record(memory)
    reference_time = (
        memory.retrieval_updated_at or memory.updated_at or memory.created_at
    ) + timedelta(seconds=1)
    memory_service = SocMemoryService(
        record_repository=repository,
        now_provider=lambda: reference_time,
    )
    registry = build_soc_memory_profile_registry()

    variants = [
        _variant(
            "baseline_same_ips",
            "基线：相同规则、行为、环境和 IP",
            base_request,
            expected="decision_applicable",
        ),
        _variant(
            "source_ip_changed",
            "仅源 IP 改变，稳定行为保持不变",
            _replace_request_ips(
                base_request,
                {source_ip: SOURCE_IP_REPLACEMENT},
            ),
            expected="decision_applicable",
        ),
        _variant(
            "destination_ip_changed",
            "仅目的 IP 改变，稳定行为保持不变",
            _replace_request_ips(
                base_request,
                {destination_ip: DESTINATION_IP_REPLACEMENT},
            ),
            expected="decision_applicable",
        ),
        _variant(
            "both_ips_changed",
            "源和目的 IP 同时改变，稳定行为保持不变",
            _replace_request_ips(
                base_request,
                {
                    source_ip: SOURCE_IP_REPLACEMENT,
                    destination_ip: DESTINATION_IP_REPLACEMENT,
                },
            ),
            expected="decision_applicable",
        ),
        _variant(
            "same_ips_partial_behavior",
            "IP 和规则不变，但场景改变；仅 TCP 行为组件重合",
            _change_behavior(
                base_request,
                protocol="tcp",
                scenario_type="lateral_movement",
            ),
            expected="context_only",
        ),
        _variant(
            "same_ips_different_behavior",
            "IP 和规则不变，但协议与场景均改变",
            _change_behavior(
                base_request,
                protocol="udp",
                scenario_type="credential_abuse",
                techniques=["T1110"],
            ),
            expected="not_retrieved",
        ),
        _variant(
            "same_ips_different_rule",
            "IP 和行为不变，但检测规则改变",
            _change_detection(base_request),
            expected="not_retrieved",
        ),
        _variant(
            "same_ips_different_environment",
            "IP、规则和行为不变，但环境由 PRD 改为 STG",
            base_request.model_copy(
                update={"environment": "stg"},
                deep=True,
            ),
            expected="not_retrieved",
        ),
    ]

    case_reports: list[dict[str, Any]] = []
    for item in variants:
        request = item["request"]
        profile = registry.resolve_request(request)
        query = memory_query_from_analysis_request(request, profile=profile)
        retrieval = memory_service.find_relevant_records(query)
        enriched = ConfirmedMemoryAnalysisRequestEnricher(
            memory_service,
            profile_registry=registry,
            environment=request.environment,
        )(request)
        memory_context = [
            context
            for context in enriched.context_catalog
            if context.kind is AnalysisContextReferenceKind.CONFIRMED_MEMORY
        ]
        match = retrieval.matches[0] if retrieval.matches else None
        context = memory_context[0] if memory_context else None
        applicability = match.applicability_report if match is not None else None
        actual = _actual_outcome(context)
        expected = item["expected"]
        case_reports.append(
            {
                "case_id": item["case_id"],
                "description_zh": item["description_zh"],
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
                "query_facets": {
                    key: query.facets.get(key, [])
                    for key in (
                        "detection_key",
                        "behavior_component",
                        "behavior_fingerprint",
                        "environment",
                        "role_entity",
                        "entity",
                    )
                },
                "retrieval": {
                    "returned_count": retrieval.returned_count,
                    "returned_context_only_count": retrieval.returned_context_only_count,
                    "skipped_not_applicable": retrieval.skipped_not_applicable,
                    "score": match.score if match is not None else None,
                    "matched_facets": match.matched_facets if match is not None else {},
                    "applicability_status": (
                        applicability.status.value
                        if applicability is not None
                        else None
                    ),
                    "context_only_allowed": (
                        applicability.context_only_allowed
                        if applicability is not None
                        else False
                    ),
                    "matched_required_facets": (
                        applicability.matched_required_facets
                        if applicability is not None
                        else {}
                    ),
                    "missing_required_facet_keys": (
                        applicability.missing_required_facet_keys
                        if applicability is not None
                        else []
                    ),
                    "decision_directive_applicable": bool(
                        context is not None
                        and context.metadata.get("decision_directive_applicable")
                        is True
                    ),
                },
            }
        )

    baseline = case_reports[0]
    positive_cases = case_reports[1:4]
    negative_cases = case_reports[4:]
    baseline_fingerprint = baseline["query_facets"]["behavior_fingerprint"]
    checks = {
        "cross_ip_fingerprint_is_stable": all(
            item["query_facets"]["behavior_fingerprint"] == baseline_fingerprint
            for item in positive_cases
        ),
        "all_cross_ip_cases_keep_decision_authority": all(
            item["actual"] == "decision_applicable" for item in positive_cases
        ),
        "both_ip_change_has_no_role_ip_overlap": not bool(
            case_reports[3]["retrieval"]["matched_facets"].get("role_entity")
        ),
        "same_ip_semantic_controls_have_no_decision_authority": all(
            item["actual"] != "decision_applicable" for item in negative_cases
        ),
        "partial_behavior_is_context_only": (
            case_reports[4]["actual"] == "context_only"
        ),
        "different_behavior_is_not_retrieved": (
            case_reports[5]["actual"] == "not_retrieved"
        ),
        "different_rule_is_not_retrieved": (
            case_reports[6]["actual"] == "not_retrieved"
        ),
        "different_environment_is_not_retrieved": (
            case_reports[7]["actual"] == "not_retrieved"
        ),
        "all_case_expectations_pass": all(item["passed"] for item in case_reports),
    }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "passed" if all(checks.values()) else "failed",
        "simulation": True,
        "llm_calls": 0,
        "memory_id": memory.memory_id,
        "memory_version": memory.version,
        "base_alert_id": base_run.alert_id,
        "base_source_ip": source_ip,
        "base_destination_ip": destination_ip,
        "replacement_source_ip": SOURCE_IP_REPLACEMENT,
        "replacement_destination_ip": DESTINATION_IP_REPLACEMENT,
        "boundary": (
            "This controlled matrix validates deterministic Memory retrieval and "
            "decision applicability only. It is not an analyst-labeled precision "
            "estimate over production alerts."
        ),
        "checks": checks,
        "cases": case_reports,
    }
    _write_json(output_dir / "cross-ip-generalization.json", report)
    _write_markdown(output_dir / "SUMMARY.md", report)
    return report


def _variant(
    case_id: str,
    description_zh: str,
    request: LLMAnalysisRequest,
    *,
    expected: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "description_zh": description_zh,
        "request": request.model_copy(
            update={"alert_id": f"{request.alert_id}-GEN-{case_id.upper()}"},
            deep=True,
        ),
        "expected": expected,
    }


def _network_endpoints(request: LLMAnalysisRequest) -> tuple[str, str]:
    source_ip = request.canonical_entities.network.source_ip
    destination_ip = request.canonical_entities.network.destination_ip
    if not source_ip or not destination_ip:
        raise ValueError("base request requires canonical source and destination IPs")
    return source_ip, destination_ip


def _apply_governed_scope(
    request: LLMAnalysisRequest,
    memory: SocMemoryRecord,
) -> LLMAnalysisRequest:
    applicability = memory.applicability
    assert applicability is not None
    environments = applicability.required_facets.get("environment", [])
    if len(environments) != 1:
        raise ValueError(
            "confirmed Pattern Memory requires exactly one governed environment"
        )
    tenant_id = memory.tenant_id
    if not tenant_id and memory.tenant_scope != "global":
        tenant_id = memory.tenant_scope
    if not tenant_id:
        raise ValueError("confirmed Pattern Memory requires a governed tenant")
    return request.model_copy(
        update={
            "tenant_id": tenant_id,
            "environment": environments[0],
        },
        deep=True,
    )


def _replace_request_ips(
    request: LLMAnalysisRequest,
    replacements: Mapping[str, str],
) -> LLMAnalysisRequest:
    payload = request.model_dump(mode="json")
    return LLMAnalysisRequest.model_validate(_replace_strings(payload, replacements))


def _replace_strings(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_strings(item, replacements) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, str):
        for source, destination in replacements.items():
            value = value.replace(source, destination)
    return value


def _change_behavior(
    request: LLMAnalysisRequest,
    *,
    protocol: str,
    scenario_type: str,
    techniques: list[str] | None = None,
) -> LLMAnalysisRequest:
    payload = request.model_dump(mode="json")
    network = payload["canonical_entities"]["network"]
    network["protocol"] = protocol
    for observation in network.get("observations", []):
        observation["protocol"] = protocol
    hypotheses = payload["fact_reconstruction"].get("scenario_hypotheses", [])
    template = (
        copy.deepcopy(hypotheses[0])
        if hypotheses
        else {
            "status": "tentative",
            "confidence": 0.8,
            "evidence_paths": ["simulation.behavior"],
        }
    )
    template["scenario_type"] = scenario_type
    template["rationale"] = "controlled negative behavior variant"
    payload["fact_reconstruction"]["scenario_hypotheses"] = [template]
    if techniques is not None:
        payload["classification"]["technique"] = techniques
    return LLMAnalysisRequest.model_validate(payload)


def _change_detection(request: LLMAnalysisRequest) -> LLMAnalysisRequest:
    payload = request.model_dump(mode="json")
    payload["detection"] = {
        **payload["detection"],
        "rule_code": "RPAADM_DIFFERENT",
        "rule_name": "不同检测规则",
        "detection_key": "sec_guard_apt_detail:rule_code:rpaadm_different",
    }
    for mention in payload.get("extracted_entities", {}).get("mentions", []):
        kind = mention.get("kind")
        if kind == "rule_code":
            mention.update(
                value="RPAADM_DIFFERENT",
                key="rule_code:RPAADM_DIFFERENT",
            )
        elif kind == "rule_name":
            mention.update(value="不同检测规则", key="rule_name:不同检测规则")
        elif kind == "rule":
            mention.update(
                value="sec_guard_apt_detail:rule_code:rpaadm_different",
                key="rule:controlled-different-rule",
            )
    return LLMAnalysisRequest.model_validate(payload)


def _actual_outcome(context: Any | None) -> str:
    if context is None:
        return "not_retrieved"
    if context.metadata.get("decision_directive_applicable") is True:
        return "decision_applicable"
    if (
        context.metadata.get("context_only") is True
        or context.metadata.get("applicability_status")
        is SocMemoryApplicabilityStatus.PARTIAL.value
    ):
        return "context_only"
    return "retrieved_without_decision_authority"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _write_markdown(path: Path, report: Mapping[str, Any]) -> None:
    lines = [
        "# Pattern Memory Cross-IP Generalization",
        "",
        f"- Status: `{report['status']}`",
        f"- Memory: `{report['memory_id']}@v{report['memory_version']}`",
        "- Data: `simulation`",
        "- LLM calls: `0`",
        "",
        "| Case | Expected | Actual | Score | Pass |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for item in report["cases"]:
        score = item["retrieval"]["score"]
        lines.append(
            f"| `{item['case_id']}` | `{item['expected']}` | "
            f"`{item['actual']}` | `{score if score is not None else '-'}` | "
            f"{'yes' if item['passed'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Checks",
            "",
            *[
                f"- [{'x' if passed else ' '}] `{name}`"
                for name, passed in report["checks"].items()
            ],
            "",
            str(report["boundary"]),
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-item", type=Path, required=True)
    parser.add_argument("--confirmed-memory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = validate_pattern_memory_generalization(
        load_analysis_run(args.input_item.expanduser().resolve()),
        load_confirmed_memory(args.confirmed_memory.expanduser().resolve()),
        output_dir=args.output_dir.expanduser().resolve(),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
