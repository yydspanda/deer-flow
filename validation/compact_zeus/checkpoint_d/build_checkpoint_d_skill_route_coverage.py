#!/usr/bin/env python3
"""Audit bounded SOC skill routing across the Checkpoint D corpus."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (  # noqa: E402
    canonical_sha256,
    sha256_file,
    write_json_atomic,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.contracts import (  # noqa: E402
    SensitiveEvidenceMode,
    SocSkillRecommendation,
    SocSkillResolution,
)
from soc_agent.core.runtime import inspect_alert_normalization  # noqa: E402
from soc_agent.pipeline.analysis_context import (  # noqa: E402
    build_llm_analysis_request,
    resolve_skill_context_for_request,
)
from soc_agent.pipeline.fact_reconstructor import reconstruct_facts  # noqa: E402
from soc_agent.skills import (  # noqa: E402
    SOC_ALERT_TRIAGE_SKILL,
    SOC_EMAIL_PHISHING_TRIAGE_SKILL,
    SOC_ENDPOINT_TRIAGE_SKILL,
    SOC_LEAD_AGENT_SKILLS,
    SOC_NETWORK_APT_TRIAGE_SKILL,
    SOC_WEB_APPLICATION_TRIAGE_SKILL,
    SocSkillResolver,
    build_soc_skill_context,
)

SCHEMA_VERSION = "soc.validation.checkpoint_d.skill_route_coverage.v2"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_PATH = (
    ROOT
    / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
    / "step-d6-skill-route-coverage"
    / "skill-route-coverage.json"
)
_ENDPOINT_SOURCES = {"edr", "xdr", "hids"}
_NETWORK_SOURCES = {"nids", "ndr", "threat_intel"}
_WEB_SOURCES = {"waf", "f5"}


def build_skill_route_coverage(
    corpus: pd.DataFrame,
    *,
    corpus_path: Path,
    corpus_file_sha256: str,
) -> dict[str, Any]:
    """Replay deterministic D1-D5 boundaries for every corpus row; never call LLM."""

    skill_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    selection_count_distribution: Counter[int] = Counter()
    source_skill_counts: dict[str, Counter[str]] = defaultdict(Counter)
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    baseline_missing: list[str] = []
    baseline_only: list[str] = []
    http_route_misses: list[str] = []
    email_route_misses: list[str] = []
    package_projection_mismatches: list[str] = []
    asset_only_endpoint_misroutes: list[str] = []
    keyword_only_cross_domain_misroutes: list[dict[str, Any]] = []

    for row_index, row in corpus.iterrows():
        alert_id = _optional_string(row.get("alert_id")) or f"row-{row_index}"
        row_key = f"{row_index}:{alert_id}"
        topic = _optional_string(row.get("topic")) or "unknown"
        try:
            alert_data = _alert_data(row, row_key=row_key)
            inspection = inspect_alert_normalization(alert_data)
            facts = reconstruct_facts(inspection.alert)
            request = build_llm_analysis_request(
                inspection.alert,
                inspection.entities,
                facts,
                sensitive_evidence_mode=SensitiveEvidenceMode.REDACT,
            )
            resolution = SocSkillResolver().resolve_for_analysis_request(request)
            context = resolve_skill_context_for_request(request)
            resolution_names = [item.skill_name for item in resolution.selected_skills]
            context_names = [item.skill_name for item in context.selected_skills]
            source_type = request.source.source_type.value
            has_http = _has_http(request)
            has_email = _has_email(request)
            has_endpoint = _has_endpoint(
                request,
                extracted_entities=inspection.entities,
                source_type=source_type,
            )

            source_counts[source_type] += 1
            selection_count_distribution[len(context_names)] += 1
            skill_counts.update(context_names)
            source_skill_counts[source_type].update(context_names)
            if SOC_ALERT_TRIAGE_SKILL not in context_names:
                baseline_missing.append(row_key)
            if context_names == [SOC_ALERT_TRIAGE_SKILL]:
                baseline_only.append(row_key)
            if has_http and SOC_WEB_APPLICATION_TRIAGE_SKILL not in context_names:
                http_route_misses.append(row_key)
            if has_email and SOC_EMAIL_PHISHING_TRIAGE_SKILL not in context_names:
                email_route_misses.append(row_key)
            if resolution_names != context_names or context.notes:
                package_projection_mismatches.append(row_key)
            if (
                inspection.entities.assets
                and not has_endpoint
                and SOC_ENDPOINT_TRIAGE_SKILL in context_names
            ):
                asset_only_endpoint_misroutes.append(row_key)
            for recommendation in resolution.selected_skills:
                if _is_keyword_only_cross_domain_route(source_type, recommendation):
                    keyword_only_cross_domain_misroutes.append(
                        {
                            "row_key": row_key,
                            "skill_name": recommendation.skill_name,
                            "matched_fields": list(recommendation.matched_fields),
                        }
                    )

            records.append(
                {
                    "row_key": row_key,
                    "alert_id": alert_id,
                    "topic": topic,
                    "source_type": source_type,
                    "selected_skills": [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in resolution.selected_skills
                    ],
                    "projected_skill_names": context_names,
                    "skill_context_sha256": canonical_sha256(
                        context.model_dump(mode="json", exclude_none=True)
                    ),
                    "estimated_token_count": context.total_estimated_token_count,
                    "entity_counts": {
                        "ips": len(inspection.entities.ips),
                        "domains": len(inspection.entities.domains),
                        "urls": len(inspection.entities.urls),
                        "emails": len(inspection.entities.emails),
                        "processes": len(inspection.entities.processes),
                        "users": len(inspection.entities.users),
                        "hosts": len(inspection.entities.hosts),
                        "assets": len(inspection.entities.assets),
                    },
                    "routing_features": {
                        "has_http": has_http,
                        "has_email": has_email,
                        "has_endpoint": has_endpoint,
                        "conflict_count": request.conflict_count,
                        "high_value_gap_count": len(
                            request.evidence_coverage.high_value_gaps
                        ),
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - audit must report every failed row
            failures.append(
                {
                    "row_key": row_key,
                    "alert_id": alert_id,
                    "topic": topic,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    package_inventory = _package_inventory()
    checks = {
        "all_rows_processed": len(records) == len(corpus),
        "no_row_failures": not failures,
        "baseline_selected_for_every_row": not baseline_missing,
        "typed_http_always_routes_web_skill": not http_route_misses,
        "typed_email_always_routes_email_skill": not email_route_misses,
        "all_selected_skills_project_from_packages": not package_projection_mismatches,
        "asset_only_entities_do_not_route_endpoint": not asset_only_endpoint_misroutes,
        "ambiguous_keywords_do_not_cross_known_source_domains": not keyword_only_cross_domain_misroutes,
        "all_profile_skill_packages_project": package_inventory["all_projected"],
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    if failed_checks:
        status = "failed"
    elif baseline_only:
        status = "passed_with_baseline_only_routes"
    else:
        status = "passed"

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "full_corpus_deterministic_d1_d5_replay",
                "typed_evidence_route_coverage",
                "host_asset_route_regression",
                "source_aware_keyword_route_regression",
                "skill_package_projection_inventory",
            ],
            "not_performed": [
                "prompt_rendering",
                "analyzer_or_llm",
                "evidence_grounding",
                "decision_policy",
                "persistence",
            ],
            "classification": "offline_evaluation_not_runtime_node",
        },
        "input": {
            "corpus_path": _relative_path(corpus_path),
            "corpus_sha256": corpus_file_sha256,
            "row_count": len(corpus),
        },
        "acceptance": {
            "status": status,
            "failed_checks": failed_checks,
            "checks": checks,
            "processed_count": len(records),
            "failure_count": len(failures),
            "baseline_only_count": len(baseline_only),
        },
        "coverage": {
            "skill_selection_counts": dict(sorted(skill_counts.items())),
            "source_type_counts": dict(sorted(source_counts.items())),
            "selection_count_distribution": {
                str(key): value
                for key, value in sorted(selection_count_distribution.items())
            },
            "source_skill_counts": {
                source: dict(sorted(counts.items()))
                for source, counts in sorted(source_skill_counts.items())
            },
        },
        "findings": {
            "baseline_missing": baseline_missing,
            "baseline_only": baseline_only,
            "http_route_misses": http_route_misses,
            "email_route_misses": email_route_misses,
            "package_projection_mismatches": package_projection_mismatches,
            "asset_only_endpoint_misroutes": asset_only_endpoint_misroutes,
            "keyword_only_cross_domain_misroutes": keyword_only_cross_domain_misroutes,
            "failures": failures,
        },
        "profile_skill_package_inventory": package_inventory,
        "routes": records,
    }


def _is_keyword_only_cross_domain_route(
    source_type: str,
    recommendation: SocSkillRecommendation,
) -> bool:
    if not recommendation.matched_fields or not all(
        matched_field.startswith("keyword:")
        for matched_field in recommendation.matched_fields
    ):
        return False
    if source_type in _ENDPOINT_SOURCES:
        return recommendation.skill_name in {
            SOC_NETWORK_APT_TRIAGE_SKILL,
            SOC_WEB_APPLICATION_TRIAGE_SKILL,
        }
    if source_type in _NETWORK_SOURCES:
        return recommendation.skill_name == SOC_ENDPOINT_TRIAGE_SKILL
    if source_type in _WEB_SOURCES:
        return recommendation.skill_name in {
            SOC_ENDPOINT_TRIAGE_SKILL,
            SOC_NETWORK_APT_TRIAGE_SKILL,
        }
    return False


def _package_inventory() -> dict[str, Any]:
    resolution = SocSkillResolution(
        selected_skills=[
            SocSkillRecommendation(
                skill_name=skill_name,
                reason="profile package inventory",
                confidence=1.0,
                matched_fields=["profile"],
            )
            for skill_name in SOC_LEAD_AGENT_SKILLS
        ],
        available_agent_skills=list(SOC_LEAD_AGENT_SKILLS),
    )
    context = build_soc_skill_context(resolution)
    projected = {item.skill_name: item for item in context.selected_skills}
    return {
        "configured_skill_names": list(SOC_LEAD_AGENT_SKILLS),
        "projected_skill_names": list(projected),
        "missing_skill_names": [
            skill_name
            for skill_name in SOC_LEAD_AGENT_SKILLS
            if skill_name not in projected
        ],
        "all_projected": len(projected) == len(SOC_LEAD_AGENT_SKILLS)
        and not context.notes,
        "items": [
            {
                "skill_name": item.skill_name,
                "guidance_source": item.guidance_source,
                "estimated_token_count": item.estimated_token_count,
                "token_budget": item.token_budget,
                "guidance_hash": item.guidance_hash,
                "package_hash": item.package_hash,
            }
            for item in context.selected_skills
        ],
        "notes": context.notes,
    }


def _alert_data(row: pd.Series, *, row_key: str) -> Mapping[str, Any]:
    full_data = row.get("alert_full_data")
    if not isinstance(full_data, Mapping):
        raise TypeError(f"{row_key}: alert_full_data must be an object")
    alert_data = full_data.get("alert_data")
    if not isinstance(alert_data, Mapping):
        raise TypeError(f"{row_key}: alert_data must be an object")
    return alert_data


def _has_http(request: Any) -> bool:
    http = request.canonical_entities.http
    return bool(
        http.observations
        or any(
            value is not None
            for value in (
                http.method,
                http.host,
                http.path,
                http.url,
                http.protocol,
                http.port,
                http.status_code,
                http.user_agent,
                http.referer,
                http.x_forwarded_for,
            )
        )
    )


def _has_email(request: Any) -> bool:
    email = request.canonical_entities.email
    return bool(
        email
        and (
            email.observations
            or email.message_id
            or email.sender_addresses
            or email.recipient_addresses
            or email.cc_addresses
            or email.subject
            or email.links
            or email.attachment_names
        )
    )


def _has_endpoint(
    request: Any,
    *,
    extracted_entities: Any,
    source_type: str,
) -> bool:
    process = request.canonical_entities.process
    user = request.canonical_entities.user
    host = request.canonical_entities.host
    return bool(
        source_type in _ENDPOINT_SOURCES
        or extracted_entities.processes
        or extracted_entities.hosts
        or extracted_entities.users
        or process.observations
        or any(
            value is not None
            for value in (
                process.process_name,
                process.process_id,
                process.process_path,
                process.command_line,
                process.parent_process_name,
                process.parent_process_id,
                process.parent_command_line,
                process.md5,
                process.sha256,
                user.username,
                user.user_id,
                user.um_account,
                user.src_user,
                user.dst_user,
                host.host_name,
                host.host_id,
            )
        )
        or host.ip_addresses
    )


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    corpus = load_dataframe_pickle(args.corpus)
    report = build_skill_route_coverage(
        corpus,
        corpus_path=args.corpus,
        corpus_file_sha256=sha256_file(args.corpus),
    )
    write_json_atomic(report, args.output)
    print(
        json.dumps(
            {
                "output": _relative_path(args.output),
                "status": report["acceptance"]["status"],
                "failed_checks": report["acceptance"]["failed_checks"],
                "processed_count": report["acceptance"]["processed_count"],
                "failure_count": report["acceptance"]["failure_count"],
                "skill_selection_counts": report["coverage"]["skill_selection_counts"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if report["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
