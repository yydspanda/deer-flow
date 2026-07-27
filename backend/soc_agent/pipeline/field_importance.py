"""Configurable high-value evidence-to-canonical mapping expectations."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from fnmatch import fnmatch
from typing import Any

from pydantic import ValidationError

from soc_agent.contracts import (
    AlertInput,
    EvidenceCoverageGap,
    EvidenceFieldImportance,
    EvidenceFieldImportanceRule,
    EvidenceInputPolicy,
    EvidenceLayer,
    ParsedRawMessageEvidence,
)

DEFAULT_EVIDENCE_FIELD_IMPORTANCE_RULES: tuple[EvidenceFieldImportanceRule, ...] = (
    EvidenceFieldImportanceRule(
        rule_id="http.user_agent",
        source_patterns=["decoded.payload.req_header.headers.user-agent*"],
        expected_target="entities.http.user_agent",
        reason="decoded HTTP User-Agent should be represented in the canonical HTTP entity",
    ),
    EvidenceFieldImportanceRule(
        rule_id="http.forwarded_chain",
        source_patterns=["decoded.payload.req_header.forwarded_chain*"],
        expected_target="entities.http.x_forwarded_for",
        reason="decoded forwarded chain should be represented in the canonical HTTP entity",
        importance=EvidenceFieldImportance.CRITICAL,
    ),
)


class EvidenceFieldImportanceRegistry:
    """Evaluate configurable field-path expectations without vendor aliases in core logic."""

    def __init__(self, rules: Sequence[EvidenceFieldImportanceRule] = DEFAULT_EVIDENCE_FIELD_IMPORTANCE_RULES) -> None:
        self._rules = tuple(rules)

    @classmethod
    def for_alert(cls, alert: AlertInput) -> EvidenceFieldImportanceRegistry:
        configured = alert.extensions.get("field_importance_rules")
        if not isinstance(configured, list):
            return cls()
        rules = list(DEFAULT_EVIDENCE_FIELD_IMPORTANCE_RULES)
        for value in configured:
            try:
                rules.append(EvidenceFieldImportanceRule.model_validate(value))
            except ValidationError:
                continue
        deduplicated = {rule.rule_id: rule for rule in rules}
        return cls(tuple(deduplicated.values()))

    @property
    def rules(self) -> tuple[EvidenceFieldImportanceRule, ...]:
        return self._rules

    def find_gaps(
        self,
        alert: AlertInput,
        parsed_by_path: Mapping[str, ParsedRawMessageEvidence],
    ) -> list[EvidenceCoverageGap]:
        gaps: list[EvidenceCoverageGap] = []
        target_cache: dict[str, Any] = {}
        for source_path, evidence_paths in _evidence_source_views(
            alert,
            parsed_by_path,
        ):
            for rule in self._rules:
                if rule.source_types and alert.source.source_type not in rule.source_types:
                    continue
                matching_paths = sorted(path for path in evidence_paths if any(fnmatch(path, pattern) for pattern in rule.source_patterns))
                if not matching_paths:
                    continue
                target_value = target_cache.setdefault(
                    rule.expected_target,
                    _resolve_model_path(alert, rule.expected_target),
                )
                if _has_value(target_value):
                    continue
                gaps.append(
                    EvidenceCoverageGap(
                        field_path=f"{source_path}#{matching_paths[0]}",
                        expected_target=rule.expected_target,
                        reason=rule.reason,
                        rule_id=rule.rule_id,
                        importance=rule.importance.value,
                    )
                )
        return list({(gap.rule_id, gap.field_path, gap.expected_target): gap for gap in gaps}.values())


def _evidence_source_views(
    alert: AlertInput,
    parsed_by_path: Mapping[str, ParsedRawMessageEvidence],
) -> list[tuple[str, list[str]]]:
    views = [(source_path, _evidence_leaf_paths(parsed)) for source_path, parsed in parsed_by_path.items()]
    configured_policy = alert.extensions.get("evidence_input_policy")
    try:
        policy = EvidenceInputPolicy.model_validate(configured_policy)
    except ValidationError:
        return views
    if policy.selected_layer is not EvidenceLayer.RAW_STRUCTURED or not policy.selected_input_path:
        return views
    selected = _resolve_payload_path(alert.raw, policy.selected_input_path)
    if not isinstance(selected, Mapping):
        return views
    views.append(
        (
            policy.selected_input_path,
            [f"structured.{path}" for path in _flatten_leaves(selected)],
        )
    )
    return views


def _evidence_leaf_paths(parsed: ParsedRawMessageEvidence) -> list[str]:
    paths: list[str] = []
    for namespace, value in (
        ("parsed", parsed.fields),
        ("decoded", parsed.decoded_fields),
        ("repaired", parsed.repaired_fields),
    ):
        paths.extend(f"{namespace}.{path}" for path in _flatten_leaves(value))
    return paths


def _flatten_leaves(value: Any, path: str = "") -> list[str]:
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            result.extend(_flatten_leaves(item, child))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_flatten_leaves(item, f"{path}[{index}]"))
        return result or ([path] if path and _has_value(value) else [])
    return [path] if path and _has_value(value) else []


def _resolve_model_path(value: Any, path: str) -> Any:
    current = value
    for segment in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(segment)
        else:
            current = getattr(current, segment, None)
        if current is None:
            return None
    return current


def _resolve_payload_path(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for segment in path.split("."):
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", segment)
        if match is None:
            return None
        key, index = match.groups()
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
        if index is not None:
            if not isinstance(value, list) or int(index) >= len(value):
                return None
            value = value[int(index)]
    return value


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


__all__ = ["DEFAULT_EVIDENCE_FIELD_IMPORTANCE_RULES", "EvidenceFieldImportanceRegistry"]
