"""Offline-only mapping suggestion workflow for normalization maintainers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from time import perf_counter
from typing import Any, Literal

from json_repair import loads as repair_json_loads
from pydantic import ValidationError

from soc_agent.contracts import (
    AnalysisRun,
    EvidenceCoverageReport,
    NormalizationMappingSuggestion,
    NormalizationSuggestionPrompt,
    NormalizationSuggestionReport,
    NormalizationSuggestionStatus,
)
from soc_agent.llm.analyzer import LLMChatClient

_ALLOWED_TARGET_PATHS = (
    "classification.category",
    "classification.severity",
    "detection.rule_category",
    "detection.rule_code",
    "detection.rule_name",
    "entities.file.file_name",
    "entities.file.file_path",
    "entities.file.md5",
    "entities.file.sha1",
    "entities.file.sha256",
    "entities.host.asset_group",
    "entities.host.asset_id",
    "entities.host.host_id",
    "entities.host.host_name",
    "entities.host.ip_addresses",
    "entities.http.host",
    "entities.http.method",
    "entities.http.path",
    "entities.http.status_code",
    "entities.http.url",
    "entities.http.user_agent",
    "entities.http.x_forwarded_for",
    "entities.network.destination_ip",
    "entities.network.direction",
    "entities.network.domain",
    "entities.network.dst_port",
    "entities.network.protocol",
    "entities.network.source_ip",
    "entities.network.src_port",
    "entities.network.url",
    "entities.process.command_line",
    "entities.process.parent_command_line",
    "entities.process.parent_process_name",
    "entities.process.process_name",
    "entities.process.process_path",
    "entities.threat.iocs",
    "entities.user.dst_user",
    "entities.user.src_user",
    "entities.user.um_account",
    "entities.user.user_id",
    "entities.user.username",
)


def build_normalization_suggestion_prompt(run: AnalysisRun) -> NormalizationSuggestionPrompt:
    """Build a sanitized prompt from path-level evidence, without raw values."""

    coverage = _require_coverage(run)
    source_paths = sorted(set(coverage.parsed_field_paths + coverage.decoded_field_paths + coverage.repaired_field_paths))
    source_hash = _source_report_hash(run)
    return NormalizationSuggestionPrompt(
        source_report_hash=source_hash,
        system_prompt=(
            "You are an offline SOC normalization mapping reviewer. Suggest candidate mappings only. "
            "Never invent source paths, never propose executable code, and never claim that a suggestion "
            "is approved or safe to auto-apply. Return one JSON object with a suggestions array."
        ),
        user_prompt=json.dumps(
            {
                "task": "Map observed source field paths to the allowed vendor-neutral canonical targets.",
                "output_schema": {
                    "suggestions": [
                        {
                            "target_path": "allowed target path",
                            "source_paths": ["observed source path"],
                            "confidence": "number from 0 to 1",
                            "rationale": "short evidence-based rationale",
                            "evidence_refs": ["optional observed source path"],
                        }
                    ]
                },
                "observed_source_paths": source_paths,
                "allowed_target_paths": list(_ALLOWED_TARGET_PATHS),
                "known_coverage_gaps": [gap.model_dump(mode="json") for gap in coverage.high_value_gaps],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        observed_source_paths=source_paths,
        allowed_target_paths=list(_ALLOWED_TARGET_PATHS),
    )


def build_normalization_suggestion_report(
    run: AnalysisRun,
    *,
    response_content: Any | None = None,
    model_name: str | None = None,
    llm_source: Literal["llm_replay", "llm"] = "llm_replay",
) -> NormalizationSuggestionReport:
    """Return governed candidates; invalid paths remain visible as rejected suggestions."""

    prompt = build_normalization_suggestion_prompt(run)
    deterministic = _coverage_gap_suggestions(run)
    warnings: list[str] = []
    generated_by = "deterministic"
    replayed: list[NormalizationMappingSuggestion] = []
    if response_content is None:
        warnings.append("no LLM replay response supplied; report contains deterministic coverage-gap candidates only")
    else:
        generated_by = llm_source
        replayed, parse_warnings = _parse_replayed_suggestions(response_content, prompt=prompt)
        warnings.extend(parse_warnings)

    suggestions = _deduplicate_suggestions([*deterministic, *replayed])
    return NormalizationSuggestionReport(
        generated_by=generated_by,
        model_name=model_name,
        source_report_hash=prompt.source_report_hash,
        suggestions=suggestions,
        warnings=warnings,
        auto_apply_allowed=False,
    )


def run_live_normalization_suggestion(
    run: AnalysisRun,
    *,
    client: LLMChatClient,
    model_name: str,
) -> NormalizationSuggestionReport:
    """Call a configured model for governed, non-applying mapping candidates."""

    prompt = build_normalization_suggestion_prompt(run)
    started_at = perf_counter()
    response = client.complete(
        [
            {"role": "system", "content": prompt.system_prompt},
            {"role": "user", "content": prompt.user_prompt},
        ],
        model_name=model_name,
    )
    content = response.content if hasattr(response, "content") else response
    response_model = getattr(response, "model_name", None)
    report = build_normalization_suggestion_report(
        run,
        response_content=content,
        model_name=response_model or model_name,
        llm_source="llm",
    )
    report.duration_ms = int((perf_counter() - started_at) * 1000)
    usage = getattr(response, "usage", None)
    if isinstance(usage, Mapping):
        report.usage = dict(usage)
    metadata = getattr(response, "metadata", None)
    if isinstance(metadata, Mapping):
        report.response_metadata = dict(metadata)
    return report


def _coverage_gap_suggestions(run: AnalysisRun) -> list[NormalizationMappingSuggestion]:
    coverage = _require_coverage(run)
    return [
        NormalizationMappingSuggestion(
            target_path=gap.expected_target,
            source_paths=[gap.field_path],
            confidence=0.7 if gap.importance != "critical" else 0.8,
            rationale=f"Coverage rule {gap.rule_id or '<unspecified>'} detected an unmapped field: {gap.reason}",
            evidence_refs=[gap.field_path],
        )
        for gap in coverage.high_value_gaps
    ]


def _parse_replayed_suggestions(
    response_content: Any,
    *,
    prompt: NormalizationSuggestionPrompt,
) -> tuple[list[NormalizationMappingSuggestion], list[str]]:
    warnings: list[str] = []
    data = _response_object(response_content)
    raw_suggestions = data.get("suggestions")
    if not isinstance(raw_suggestions, list):
        raise ValueError("normalization suggestion response must contain a suggestions array")

    observed = set(prompt.observed_source_paths)
    allowed_targets = set(prompt.allowed_target_paths)
    suggestions: list[NormalizationMappingSuggestion] = []
    for index, value in enumerate(raw_suggestions):
        if not isinstance(value, Mapping):
            warnings.append(f"suggestion[{index}] ignored because it is not an object")
            continue
        try:
            suggestion = NormalizationMappingSuggestion.model_validate(value)
        except ValidationError as exc:
            warnings.append(f"suggestion[{index}] failed schema validation: {exc.errors()[0]['msg']}")
            continue
        unknown_sources = sorted(set(suggestion.source_paths) - observed)
        if suggestion.target_path not in allowed_targets or unknown_sources:
            reasons: list[str] = []
            if suggestion.target_path not in allowed_targets:
                reasons.append("target path is not in the canonical whitelist")
            if unknown_sources:
                reasons.append(f"{len(unknown_sources)} source path(s) were not observed")
            suggestion.status = NormalizationSuggestionStatus.REJECTED
            suggestion.rationale = f"Rejected at offline boundary: {'; '.join(reasons)}. {suggestion.rationale}"
        suggestions.append(suggestion)
    return suggestions, warnings


def _response_object(response_content: Any) -> dict[str, Any]:
    if isinstance(response_content, Mapping):
        return dict(response_content)
    text = str(response_content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = repair_json_loads(text, skip_json_loads=True)
    if not isinstance(parsed, dict):
        raise ValueError("normalization suggestion response must be a JSON object")
    return parsed


def _source_report_hash(run: AnalysisRun) -> str:
    coverage = _require_coverage(run)
    payload = {
        "normalization": run.normalization_report.model_dump(mode="json") if run.normalization_report else None,
        "coverage": coverage.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_coverage(run: AnalysisRun) -> EvidenceCoverageReport:
    if run.llm_analysis_request is None:
        raise ValueError("analysis run has no bounded analysis request")
    return run.llm_analysis_request.evidence_coverage


def _deduplicate_suggestions(
    suggestions: list[NormalizationMappingSuggestion],
) -> list[NormalizationMappingSuggestion]:
    result: dict[tuple[str, tuple[str, ...]], NormalizationMappingSuggestion] = {}
    for suggestion in suggestions:
        key = (suggestion.target_path, tuple(sorted(suggestion.source_paths)))
        existing = result.get(key)
        if existing is None or suggestion.confidence > existing.confidence:
            result[key] = suggestion
    return sorted(result.values(), key=lambda item: (item.status.value, item.target_path, item.source_paths))


__all__ = ["build_normalization_suggestion_prompt", "build_normalization_suggestion_report"]
