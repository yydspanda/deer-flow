"""Schema observation and evidence-usage coverage for bounded analysis input."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from soc_agent.contracts import (
    AlertInput,
    BoundedAnalysisEvidence,
    EvidenceCoverageGap,
    EvidenceCoverageOmission,
    EvidenceCoverageReport,
    EvidenceInputPolicy,
    EvidenceLayer,
    FactReconstructionResult,
    MessageSchemaObservation,
    MessageSchemaStatus,
    ParsedRawMessageEvidence,
    SensitiveEvidenceMode,
)
from soc_agent.pipeline.field_importance import EvidenceFieldImportanceRegistry

_DECODED_SEPARATELY_FIELDS = frozenset(
    {
        "req_body",
        "rsp_body",
        "rule_labels",
        "req_header",
        "rsp_header",
        "request_header_str",
        "response_header_str",
        "response_hqeader_str",
    }
)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|credential|pwd)",
    re.IGNORECASE,
)


def observe_message_schemas(alert: AlertInput) -> list[MessageSchemaObservation]:
    """Describe whether each selected outer message shape was parsed."""

    parsed_by_path = _parsed_messages_by_path(alert)
    expected_paths = set(parsed_by_path)
    policy = _evidence_policy(alert)
    if policy is not None and policy.selected_layer is EvidenceLayer.RAW_MESSAGE:
        if policy.selected_input_path:
            expected_paths.add(policy.selected_input_path)
        expected_paths.update(policy.supplementary_input_paths)

    observations: list[MessageSchemaObservation] = []
    for path in sorted(expected_paths):
        parsed = parsed_by_path.get(path)
        if parsed is None:
            observations.append(
                MessageSchemaObservation(
                    source_path=path,
                    status=MessageSchemaStatus.UNSUPPORTED,
                    warnings=["selected raw message has no matching deterministic parser"],
                )
            )
            continue
        signature = _schema_signature(parsed.fields)
        observations.append(
            MessageSchemaObservation(
                source_path=path,
                parser_name=parsed.parser_name,
                parser_version=parsed.parser_version,
                schema_fingerprint=_schema_fingerprint(parsed.parser_name, parsed.parser_version, signature),
                # A ParsedRawMessageEvidence exists only after the outer message
                # parser succeeds. Nested body decode/repair warnings remain
                # visible below, but do not invalidate that outer schema.
                status=MessageSchemaStatus.RECOGNIZED,
                field_count=len(_flatten_leaves(parsed.fields)),
                warnings=parsed.warnings,
            )
        )
    return observations


def build_evidence_coverage_report(
    alert: AlertInput,
    fact: FactReconstructionResult,
    primary: BoundedAnalysisEvidence | None,
    supplementary: Sequence[BoundedAnalysisEvidence],
    *,
    highlighted_paths: Sequence[str] = (),
    compacted_paths: Sequence[str] = (),
) -> EvidenceCoverageReport:
    """Build an auditable field-path coverage report for one analysis request."""

    parsed_by_path = _parsed_messages_by_path(alert)
    message_schemas = observe_message_schemas(alert)
    parsed_paths: list[str] = []
    structured_paths: list[str] = []
    decoded_paths: list[str] = []
    repaired_paths: list[str] = []
    parsed_paths_by_message: dict[str, list[str]] = {}
    decoded_paths_by_message: dict[str, list[str]] = {}
    repaired_paths_by_message: dict[str, list[str]] = {}

    for source_path, parsed in parsed_by_path.items():
        message_parsed_paths = [f"{source_path}#parsed.{path}" for path in _flatten_leaves(parsed.fields)]
        message_decoded_paths = [f"{source_path}#decoded.{path}" for path in _flatten_leaves(parsed.decoded_fields)]
        message_repaired_paths = [f"{source_path}#repaired.{path}" for path in _flatten_leaves(parsed.repaired_fields)]
        parsed_paths_by_message[source_path] = message_parsed_paths
        decoded_paths_by_message[source_path] = message_decoded_paths
        repaired_paths_by_message[source_path] = message_repaired_paths
        parsed_paths.extend(message_parsed_paths)
        decoded_paths.extend(message_decoded_paths)
        repaired_paths.extend(message_repaired_paths)

    evidence_by_path = {item.source_path: item for item in [primary, *supplementary] if item is not None}
    highlighted_path_set = set(highlighted_paths)
    compacted_path_set = set(compacted_paths)
    projected_paths: list[str] = [*highlighted_path_set, *compacted_path_set]
    sanitized_paths: list[str] = []
    compacted_encoded_paths: list[str] = []
    truncated_evidence_paths: list[str] = []
    omissions: list[EvidenceCoverageOmission] = []

    for source_path, message_paths in parsed_paths_by_message.items():
        evidence = evidence_by_path.get(source_path)
        if evidence is None:
            omissions.extend(
                EvidenceCoverageOmission(
                    field_path=path,
                    reason="message_not_selected_for_bounded_analysis",
                )
                for path in message_paths
                if path not in compacted_path_set
            )
            continue
        projected_paths.extend(evidence.projected_field_paths)
        sanitized_paths.extend(evidence.sanitized_field_paths)
        compacted_encoded_paths.extend(item.field_path for item in evidence.encoded_span_omissions)
        omissions.extend(
            EvidenceCoverageOmission(
                field_path=path,
                reason=evidence.omission_reasons.get(path, "bounded_projection_budget"),
            )
            for path in evidence.omitted_field_paths
        )
        if evidence.truncated:
            truncated_evidence_paths.append(source_path)

        if evidence.sensitive_evidence_mode is SensitiveEvidenceMode.REDACT:
            parsed = parsed_by_path[source_path]
            for relative_path in _flatten_leaves(parsed.fields):
                full_path = f"{source_path}#parsed.{relative_path}"
                field_name = relative_path.rsplit(".", 1)[-1].lower()
                if _SENSITIVE_FIELD_RE.search(relative_path):
                    sanitized_paths.append(full_path)
                    omissions.append(
                        EvidenceCoverageOmission(
                            field_path=full_path,
                            reason="sensitive_value_redacted",
                        )
                    )
                elif field_name in _DECODED_SEPARATELY_FIELDS:
                    sanitized_paths.append(full_path)
                    decoded_value = _resolve_relative_path(
                        parsed.decoded_fields,
                        relative_path,
                    )
                    repaired_value = _resolve_relative_path(
                        parsed.repaired_fields,
                        relative_path,
                    )
                    omissions.append(
                        EvidenceCoverageOmission(
                            field_path=full_path,
                            reason=("replaced_by_decoded_projection" if decoded_value is not None else ("replaced_by_repaired_projection" if repaired_value is not None else "sanitized_string_fallback")),
                        )
                    )

    for source_path, evidence in evidence_by_path.items():
        if evidence.layer is not EvidenceLayer.RAW_STRUCTURED:
            continue
        structured_paths.extend(evidence.projected_field_paths)
        structured_paths.extend(evidence.omitted_field_paths)
        projected_paths.extend(evidence.projected_field_paths)
        sanitized_paths.extend(evidence.sanitized_field_paths)
        compacted_encoded_paths.extend(item.field_path for item in evidence.encoded_span_omissions)
        omissions.extend(
            EvidenceCoverageOmission(
                field_path=path,
                reason=evidence.omission_reasons.get(path, "bounded_projection_budget"),
            )
            for path in evidence.omitted_field_paths
        )
        if evidence.truncated:
            truncated_evidence_paths.append(source_path)

    coverage_extension = alert.extensions.get("analysis_context_coverage")
    if isinstance(coverage_extension, Mapping):
        deferred_sources = coverage_extension.get("deferred_sources")
        if isinstance(deferred_sources, list):
            for value in deferred_sources:
                if not isinstance(value, Mapping):
                    continue
                field_path = value.get("field_path")
                reason = value.get("reason")
                if isinstance(field_path, str) and field_path and isinstance(reason, str) and reason:
                    omissions.append(EvidenceCoverageOmission(field_path=field_path, reason=reason))

    canonical_paths = [item.selected_from for item in fact.canonical_field_provenance]
    fact_paths = [item.evidence_path for item in fact.role_claims]
    scenario_paths = [path for item in fact.scenario_hypotheses for path in item.evidence_paths]
    high_value_gaps = EvidenceFieldImportanceRegistry.for_alert(alert).find_gaps(alert, parsed_by_path)
    if not any(
        (
            evidence_by_path,
            highlighted_path_set,
            canonical_paths,
            fact_paths,
            scenario_paths,
        )
    ):
        high_value_gaps.append(
            EvidenceCoverageGap(
                field_path="input_payload",
                expected_target="llm_analysis_request.primary_evidence",
                reason=("upstream input evidence unavailable: no bounded raw, canonical, fact, or scenario evidence could be projected"),
                rule_id="analysis_evidence.unavailable",
                importance="critical",
            )
        )

    warnings: list[str] = []
    for observation in message_schemas:
        if observation.status is MessageSchemaStatus.UNSUPPORTED:
            warnings.append(f"unsupported message schema: {observation.source_path}")
        elif observation.status is MessageSchemaStatus.DEGRADED:
            warnings.append(f"degraded message schema: {observation.source_path}")
    if truncated_evidence_paths:
        warnings.append("bounded evidence omitted one or more fields; inspect coverage omissions for exact paths")
    if compacted_encoded_paths:
        warnings.append("bounded evidence compacted one or more encoded spans; original values remain in raw input")
    warnings.extend(f"unmapped high-value evidence: {item.field_path} -> {item.expected_target}" for item in high_value_gaps)

    parsed_paths = _sorted_unique(parsed_paths)
    structured_paths = _sorted_unique(structured_paths)
    decoded_paths = _sorted_unique(decoded_paths)
    repaired_paths = _sorted_unique(repaired_paths)
    canonical_paths = _sorted_unique(canonical_paths)
    fact_paths = _sorted_unique(fact_paths)
    scenario_paths = _sorted_unique(scenario_paths)
    projected_paths = _sorted_unique(projected_paths)
    sanitized_paths = _sorted_unique(sanitized_paths)
    compacted_encoded_paths = _sorted_unique(compacted_encoded_paths)
    projected_summary_paths = highlighted_path_set | compacted_path_set
    omissions = [item for item in {(item.field_path, item.reason): item for item in omissions}.values() if item.field_path not in projected_summary_paths]
    return EvidenceCoverageReport(
        message_schemas=message_schemas,
        structured_field_paths=structured_paths,
        parsed_field_paths=parsed_paths,
        decoded_field_paths=decoded_paths,
        repaired_field_paths=repaired_paths,
        canonical_source_paths=canonical_paths,
        fact_source_paths=fact_paths,
        scenario_source_paths=scenario_paths,
        llm_projected_paths=projected_paths,
        llm_sanitized_paths=sanitized_paths,
        llm_compacted_encoded_paths=compacted_encoded_paths,
        llm_truncated_evidence_paths=_sorted_unique(truncated_evidence_paths),
        omissions=omissions,
        high_value_gaps=high_value_gaps,
        counts={
            "message_schema_count": len(message_schemas),
            "structured_field_count": len(structured_paths),
            "parsed_field_count": len(parsed_paths),
            "decoded_field_count": len(decoded_paths),
            "repaired_field_count": len(repaired_paths),
            "canonical_source_count": len(canonical_paths),
            "fact_source_count": len(fact_paths),
            "scenario_source_count": len(scenario_paths),
            "llm_projected_count": len(projected_paths),
            "llm_highlighted_count": len(highlighted_path_set),
            "llm_compacted_observation_field_count": len(compacted_path_set),
            "llm_sanitized_count": len(sanitized_paths),
            "llm_compacted_encoded_count": len(compacted_encoded_paths),
            "llm_truncated_evidence_count": len(_sorted_unique(truncated_evidence_paths)),
            "omission_count": len(omissions),
            "high_value_gap_count": len(high_value_gaps),
        },
        warnings=_sorted_unique(warnings),
    )


def _parsed_messages_by_path(alert: AlertInput) -> dict[str, ParsedRawMessageEvidence]:
    result: dict[str, ParsedRawMessageEvidence] = {}
    values = alert.extensions.get("parsed_raw_messages")
    if not isinstance(values, list):
        return result
    for value in values:
        try:
            parsed = ParsedRawMessageEvidence.model_validate(value)
        except ValidationError:
            continue
        result[parsed.source_path] = parsed
    return result


def _evidence_policy(alert: AlertInput) -> EvidenceInputPolicy | None:
    value = alert.extensions.get("evidence_input_policy")
    if value is None:
        return None
    try:
        return EvidenceInputPolicy.model_validate(value)
    except ValidationError:
        return None


def _schema_signature(value: Any, path: str = "$") -> list[str]:
    entries = [f"{path}:{_type_name(value)}"]
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            entries.extend(_schema_signature(value[key], f"{path}.{key}"))
    elif isinstance(value, list):
        for item in value[:1]:
            entries.extend(_schema_signature(item, f"{path}[]"))
    return entries


def _schema_fingerprint(parser_name: str, parser_version: str, signature: Sequence[str]) -> str:
    payload = json.dumps([parser_name, parser_version, *signature], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _flatten_leaves(value: Any, path: str = "") -> list[str]:
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            result.extend(_flatten_leaves(item, child_path))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_flatten_leaves(item, f"{path}[{index}]"))
        return result or [path]
    return [path] if path else []


def _resolve_relative_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


__all__ = ["build_evidence_coverage_report", "observe_message_schemas"]
