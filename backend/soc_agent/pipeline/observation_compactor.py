"""Compact repeated canonical observations without discarding raw alert data."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from soc_agent.contracts import (
    AlertInput,
    CompactedObservationFact,
    CompactedObservationGroup,
    CompactedObservationProfile,
    CompactedObservationValueCount,
    CompactedObservationVariation,
    EvidenceCompactionReport,
    EvidenceInputPolicy,
    EvidenceLayer,
    ParsedRawMessageEvidence,
    SourceFieldSemantic,
)
from soc_agent.utils.hashing import stable_hash

_MAX_GROUPS = 100
_MAX_STABLE_FACTS = 80
_MAX_VARYING_FACTS = 40
_MAX_VALUES_PER_VARIATION = 12
_MAX_PROFILES_PER_GROUP = 20
_MAX_PROFILE_FACTS = 40
_MAX_FACT_VALUE_CHARS = 500
_MAX_SELECTED_EVIDENCE_PATHS = 5
_MAX_FALLBACK_SEMANTIC_FACTS = 40
_SENSITIVE_FIELD_RE = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|credential|pwd)",
    re.IGNORECASE,
)
_PROFILE_NOISE_SUFFIXES = (
    ".src_port",
    ".sensor_source_port",
    ".process_id",
    ".parent_process_id",
)
_OBSERVATION_METADATA_FIELDS = frozenset(
    {
        "observation_id",
        "evidence_path",
        "event_time",
        "flow_id",
    }
)


@dataclass
class _BoundedValue:
    value: str | int | float | bool
    truncated: bool = False


@dataclass
class _SourceObservation:
    source_path: str
    parser_names: set[str] = field(default_factory=set)
    observation_kinds: set[str] = field(default_factory=set)
    event_times: list[str] = field(default_factory=list)
    facts: dict[str, _BoundedValue] = field(default_factory=dict)
    represented_field_paths: set[str] = field(default_factory=set)
    opaque_message_hash: str | None = None


@dataclass
class _BuiltGroup:
    group: CompactedObservationGroup
    omitted_item_count: int


def build_evidence_compaction_report(
    alert: AlertInput,
    *,
    primary_evidence_path: str | None,
) -> EvidenceCompactionReport:
    """Group repeated source messages by canonical observation shape.

    The returned object is a bounded model-facing summary. ``AlertInput.raw`` and
    ``extensions.parsed_raw_messages`` remain untouched for audit and replay.
    """

    parsed_by_path = _parsed_messages_by_path(alert)
    records, typed_observation_count = _source_observations(
        alert,
        parsed_by_path=parsed_by_path,
    )
    grouped: dict[str, list[_SourceObservation]] = defaultdict(list)
    for record in records:
        grouped[_shape_key(record)].append(record)

    built_groups = [
        _build_group(group_records)
        for _, group_records in sorted(
            grouped.items(),
            key=lambda item: min(record.source_path for record in item[1]),
        )
    ]
    omitted_item_count = sum(item.omitted_item_count for item in built_groups)
    if len(built_groups) > _MAX_GROUPS:
        omitted_item_count += len(built_groups) - _MAX_GROUPS
        built_groups = built_groups[:_MAX_GROUPS]

    groups = [item.group for item in built_groups]
    represented_field_paths = sorted({field_path for record in records for field_path in record.represented_field_paths})
    represented_source_count = sum(group.occurrence_count for group in groups)
    selected_paths = _selected_evidence_paths(
        groups,
        primary_evidence_path=primary_evidence_path,
    )
    opaque_unselected_count = sum(record.opaque_message_hash is not None and record.source_path not in selected_paths for record in records)
    omitted_item_count += opaque_unselected_count
    profile_count = sum(group.profile_count for group in groups)
    collapsed_repetition_count = max(0, len(records) - profile_count)
    warnings: list[str] = []
    if omitted_item_count:
        warnings.append("observation compaction exceeded a model-facing detail budget; raw input remains available for replay")
    if represented_source_count < len(records):
        warnings.append("one or more source messages could not be represented by the bounded observation groups")
    if opaque_unselected_count:
        warnings.append("one or more unsupported message variants were not selected as full evidence; deterministic analysis must remain degraded")

    return EvidenceCompactionReport(
        source_message_count=len(records),
        typed_observation_count=typed_observation_count,
        behavior_group_count=len(groups),
        profile_count=profile_count,
        repeated_shape_message_count=max(0, len(records) - len(groups)),
        collapsed_repetition_count=collapsed_repetition_count,
        non_dominant_profile_count=sum(group.non_dominant_profile_count for group in groups),
        selected_evidence_paths=selected_paths,
        represented_field_paths=represented_field_paths[:5000],
        represented_field_count=len(represented_field_paths),
        represented_source_count=represented_source_count,
        unrepresented_source_count=max(0, len(records) - represented_source_count),
        high_value_omission_count=omitted_item_count,
        groups=groups,
        warnings=warnings,
    )


def _source_observations(
    alert: AlertInput,
    *,
    parsed_by_path: dict[str, ParsedRawMessageEvidence],
) -> tuple[list[_SourceObservation], int]:
    records: dict[str, _SourceObservation] = {
        source_path: _SourceObservation(
            source_path=source_path,
            parser_names={parsed.parser_name},
        )
        for source_path, parsed in parsed_by_path.items()
    }
    for source_path in _raw_message_paths(alert):
        record = records.setdefault(
            source_path,
            _SourceObservation(source_path=source_path),
        )
        if source_path not in parsed_by_path:
            record.observation_kinds.add("opaque_raw_message")
            record.opaque_message_hash = stable_hash(_resolve_alert_raw_path(alert, source_path))
    typed_observation_count = 0
    per_path_kind_count: Counter[tuple[str, str]] = Counter()
    for kind, observation in _typed_observations(alert):
        typed_observation_count += 1
        payload = observation.model_dump(mode="json", exclude_none=True)
        evidence_path = str(payload.pop("evidence_path"))
        source_path = evidence_path.split("#", 1)[0]
        record = records.setdefault(
            source_path,
            _SourceObservation(source_path=source_path),
        )
        record.observation_kinds.add(kind)
        event_time = payload.pop("event_time", None)
        if event_time is not None:
            record.event_times.append(str(event_time))
        for metadata_field in _OBSERVATION_METADATA_FIELDS:
            payload.pop(metadata_field, None)
        per_path_kind_count[(source_path, kind)] += 1
        occurrence = per_path_kind_count[(source_path, kind)]
        prefix = kind if occurrence == 1 else f"{kind}[{occurrence - 1}]"
        for fact_path, value in _flatten_scalars(payload, prefix=prefix):
            record.facts[fact_path] = _bound_fact_value(
                value,
                field_path=fact_path,
            )

    semantics_by_path = _semantics_by_source_path(alert, parsed_by_path)
    for source_path, record in records.items():
        typed_value_keys = {_value_key(value) for value in record.facts.values()}
        for semantic, value in semantics_by_path.get(source_path, []):
            bounded_value = _bound_fact_value(
                value,
                field_path=semantic.field_path,
            )
            if _value_key(bounded_value) in typed_value_keys:
                record.represented_field_paths.add(semantic.field_path)
        if record.facts:
            continue
        for index, (semantic, value) in enumerate(semantics_by_path.get(source_path, [])[:_MAX_FALLBACK_SEMANTIC_FACTS]):
            fact_path = f"semantic.{semantic.semantic_type}[{index}]"
            record.facts[fact_path] = _bound_fact_value(
                value,
                field_path=semantic.field_path,
            )
            record.represented_field_paths.add(semantic.field_path)
            record.observation_kinds.add("semantic_fallback")

    return sorted(records.values(), key=lambda item: item.source_path), typed_observation_count


def _typed_observations(alert: AlertInput) -> list[tuple[str, BaseModel]]:
    observations: list[tuple[str, BaseModel]] = []
    for kind, values in (
        ("network", alert.entities.network.observations),
        ("http", alert.entities.http.observations),
        ("process", alert.entities.process.observations),
        ("file", alert.entities.file.observations),
        (
            "email",
            alert.entities.email.observations if alert.entities.email is not None else [],
        ),
    ):
        observations.extend((kind, value) for value in values)
    return observations


def _shape_key(record: _SourceObservation) -> str:
    return stable_hash(
        {
            "namespace": "soc.observation_shape.v1",
            "parser_names": sorted(record.parser_names),
            "observation_kinds": sorted(record.observation_kinds),
            "fact_paths": sorted(record.facts),
            "opaque_message_hash": record.opaque_message_hash,
        }
    )


def _build_group(records: list[_SourceObservation]) -> _BuiltGroup:
    ordered_records = sorted(records, key=lambda item: item.source_path)
    fact_paths = sorted({path for record in ordered_records for path in record.facts})
    stable_facts: list[CompactedObservationFact] = []
    varying_facts: list[CompactedObservationVariation] = []
    varying_paths: list[str] = []
    omitted_item_count = 0

    for fact_path in fact_paths:
        values = [record.facts.get(fact_path) for record in ordered_records]
        counts: Counter[str] = Counter(_value_key(value) for value in values)
        keyed_values = {_value_key(value): value for value in values}
        if len(counts) == 1 and values[0] is not None:
            stable_facts.append(_fact(fact_path, values[0]))
            continue
        varying_paths.append(fact_path)
        ordered_counts = _select_value_counts(counts)
        if len(ordered_counts) < len(counts):
            omitted_item_count += len(counts) - len(ordered_counts)
        varying_facts.append(
            CompactedObservationVariation(
                field_path=fact_path,
                distinct_value_count=len(counts),
                values=[
                    CompactedObservationValueCount(
                        value=(keyed_values[key].value if keyed_values[key] is not None else "[MISSING]"),
                        occurrence_count=count,
                        truncated=(keyed_values[key].truncated if keyed_values[key] is not None else False),
                    )
                    for key, count in ordered_counts
                ],
                values_truncated=len(ordered_counts) < len(counts),
            )
        )

    profiles = _profiles(ordered_records, varying_paths)
    profile_count = len(profiles)
    selected_profiles = _select_profiles(profiles)
    if len(selected_profiles) < profile_count:
        omitted_item_count += profile_count - len(selected_profiles)
    dominant_count = max(
        (profile.occurrence_count for profile in profiles),
        default=1,
    )
    non_dominant_profile_count = sum(profile.occurrence_count < dominant_count for profile in profiles) if dominant_count > 1 else 0

    if len(stable_facts) > _MAX_STABLE_FACTS:
        omitted_item_count += len(stable_facts) - _MAX_STABLE_FACTS
        stable_facts = stable_facts[:_MAX_STABLE_FACTS]
    if len(varying_facts) > _MAX_VARYING_FACTS:
        omitted_item_count += len(varying_facts) - _MAX_VARYING_FACTS
        varying_facts = varying_facts[:_MAX_VARYING_FACTS]

    source_paths = [record.source_path for record in ordered_records]
    event_times = sorted(event_time for record in ordered_records for event_time in record.event_times)
    group_key = {
        "namespace": "soc.observation_group.v1",
        "shape": _shape_key(ordered_records[0]),
        "source_paths": source_paths,
    }
    return _BuiltGroup(
        group=CompactedObservationGroup(
            group_id=f"OG-{stable_hash(group_key)[:12].upper()}",
            parser_names=sorted({parser_name for record in ordered_records for parser_name in record.parser_names}),
            observation_kinds=sorted({kind for record in ordered_records for kind in record.observation_kinds}),
            occurrence_count=len(ordered_records),
            source_paths=source_paths[:100],
            source_path_count=len(source_paths),
            source_paths_truncated=len(source_paths) > 100,
            representative_source_path=source_paths[0],
            first_seen=event_times[0] if event_times else None,
            last_seen=event_times[-1] if event_times else None,
            stable_facts=stable_facts,
            varying_facts=varying_facts,
            profiles=selected_profiles,
            profile_count=profile_count,
            profiles_truncated=len(selected_profiles) < profile_count,
            non_dominant_profile_count=non_dominant_profile_count,
        ),
        omitted_item_count=omitted_item_count,
    )


def _profiles(
    records: list[_SourceObservation],
    varying_paths: list[str],
) -> list[CompactedObservationProfile]:
    profile_paths = [path for path in varying_paths if not path.endswith(_PROFILE_NOISE_SUFFIXES)]
    grouped: dict[str, list[_SourceObservation]] = defaultdict(list)
    profile_facts: dict[str, list[CompactedObservationFact]] = {}
    for record in records:
        facts = [_fact(path, record.facts.get(path)) for path in profile_paths]
        signature = stable_hash(
            {
                "namespace": "soc.observation_profile.v1",
                "facts": [fact.model_dump(mode="json") for fact in facts],
            }
        )
        grouped[signature].append(record)
        profile_facts[signature] = facts[:_MAX_PROFILE_FACTS]

    result = []
    for signature, profile_records in grouped.items():
        result.append(
            CompactedObservationProfile(
                profile_id=f"OP-{signature[:12].upper()}",
                occurrence_count=len(profile_records),
                representative_source_path=min(record.source_path for record in profile_records),
                varying_facts=profile_facts[signature],
            )
        )
    return sorted(
        result,
        key=lambda item: (
            -item.occurrence_count,
            item.representative_source_path,
            item.profile_id,
        ),
    )


def _select_profiles(
    profiles: list[CompactedObservationProfile],
) -> list[CompactedObservationProfile]:
    if len(profiles) <= _MAX_PROFILES_PER_GROUP:
        return profiles
    dominant = profiles[0]
    rare = sorted(
        profiles[1:],
        key=lambda item: (
            item.occurrence_count,
            item.representative_source_path,
            item.profile_id,
        ),
    )[: _MAX_PROFILES_PER_GROUP - 1]
    return [dominant, *rare]


def _select_value_counts(
    counts: Counter[str],
) -> list[tuple[str, int]]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) <= _MAX_VALUES_PER_VARIATION:
        return ordered
    dominant = ordered[0]
    rare = sorted(
        ordered[1:],
        key=lambda item: (item[1], item[0]),
    )[: _MAX_VALUES_PER_VARIATION - 1]
    return [dominant, *rare]


def _selected_evidence_paths(
    groups: list[CompactedObservationGroup],
    *,
    primary_evidence_path: str | None,
) -> list[str]:
    selected: list[str] = []
    if primary_evidence_path:
        selected.append(primary_evidence_path)
    for group in groups:
        candidates = [profile.representative_source_path for profile in group.profiles] or [group.representative_source_path]
        for candidate in candidates:
            if len(selected) >= _MAX_SELECTED_EVIDENCE_PATHS:
                break
            if candidate in selected:
                continue
            selected.append(candidate)
    return selected


def _fact(
    field_path: str,
    value: _BoundedValue | None,
) -> CompactedObservationFact:
    if value is None:
        return CompactedObservationFact(
            field_path=field_path,
            value="[MISSING]",
        )
    return CompactedObservationFact(
        field_path=field_path,
        value=value.value,
        truncated=value.truncated,
    )


def _value_key(value: _BoundedValue | None) -> str:
    if value is None:
        return "null:[MISSING]"
    return json.dumps(
        [type(value.value).__name__, value.value, value.truncated],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _bound_fact_value(
    value: Any,
    *,
    field_path: str,
) -> _BoundedValue:
    if _SENSITIVE_FIELD_RE.search(field_path):
        return _BoundedValue(value="[REDACTED]")
    if isinstance(value, bool):
        return _BoundedValue(value=value)
    if isinstance(value, int | float):
        return _BoundedValue(value=value)
    if not isinstance(value, str):
        value = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    if len(value) <= _MAX_FACT_VALUE_CHARS:
        return _BoundedValue(value=value)
    suffix = f"...[TRUNCATED chars={len(value)} sha256={stable_hash(value)[:16]}]"
    prefix_chars = max(0, _MAX_FACT_VALUE_CHARS - len(suffix))
    return _BoundedValue(
        value=f"{value[:prefix_chars]}{suffix}",
        truncated=True,
    )


def _flatten_scalars(
    value: Any,
    *,
    prefix: str,
) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        result: list[tuple[str, Any]] = []
        for key in sorted(value, key=str):
            child = f"{prefix}.{key}" if prefix else str(key)
            result.extend(_flatten_scalars(value[key], prefix=child))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_flatten_scalars(item, prefix=f"{prefix}[{index}]"))
        return result
    if value is None:
        return []
    return [(prefix, value)]


def _parsed_messages_by_path(
    alert: AlertInput,
) -> dict[str, ParsedRawMessageEvidence]:
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


def _raw_message_paths(alert: AlertInput) -> list[str]:
    value = alert.extensions.get("evidence_input_policy")
    try:
        policy = EvidenceInputPolicy.model_validate(value)
    except ValidationError:
        return []
    if policy.selected_layer is not EvidenceLayer.RAW_MESSAGE:
        return []
    return list(
        dict.fromkeys(
            path
            for path in [
                policy.selected_input_path,
                *policy.supplementary_input_paths,
            ]
            if path
        )
    )


def _resolve_alert_raw_path(alert: AlertInput, path: str) -> Any:
    value: Any = alert.raw
    for segment in path.split("."):
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", segment)
        if match is None:
            return None
        key, index = match.groups()
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
        if index is not None:
            if not isinstance(value, list) or int(index) >= len(value):
                return None
            value = value[int(index)]
    return value


def _semantics_by_source_path(
    alert: AlertInput,
    parsed_by_path: dict[str, ParsedRawMessageEvidence],
) -> dict[str, list[tuple[SourceFieldSemantic, Any]]]:
    result: dict[str, list[tuple[SourceFieldSemantic, Any]]] = defaultdict(list)
    values = alert.extensions.get("source_field_semantics")
    if not isinstance(values, list):
        return result
    for value in values:
        try:
            semantic = SourceFieldSemantic.model_validate(value)
        except ValidationError:
            continue
        if not semantic.participates_in_reasoning:
            continue
        resolved = _semantic_field_value(parsed_by_path, semantic.field_path)
        if resolved is None or resolved == "":
            continue
        source_path = semantic.field_path.split("#", 1)[0]
        result[source_path].append((semantic, resolved))
    return result


def _semantic_field_value(
    parsed_by_path: dict[str, ParsedRawMessageEvidence],
    field_path: str,
) -> Any:
    match = re.fullmatch(r"(.+)#(parsed|decoded|repaired)\.(.+)", field_path)
    if match is None:
        return None
    source_path, namespace, relative_path = match.groups()
    parsed = parsed_by_path.get(source_path)
    if parsed is None:
        return None
    root = {
        "parsed": parsed.fields,
        "decoded": parsed.decoded_fields,
        "repaired": parsed.repaired_fields,
    }[namespace]
    return _resolve_relative_path(root, relative_path)


def _resolve_relative_path(root: Any, path: str) -> Any:
    value = root
    for segment in path.split("."):
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", segment)
        if match is None:
            return None
        key, index = match.groups()
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
        if index is not None:
            if not isinstance(value, list) or int(index) >= len(value):
                return None
            value = value[int(index)]
    return value


__all__ = ["build_evidence_compaction_report"]
