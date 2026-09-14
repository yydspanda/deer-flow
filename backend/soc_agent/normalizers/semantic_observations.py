"""Merge source-bound typed proposals into the existing canonical alert contract."""

from __future__ import annotations

import html
import ipaddress
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import ValidationError

from soc_agent.contracts import AlertInput, CanonicalFieldProvenance, FileObservationRef, HttpObservationRef, NetworkObservationRef, NormalizationAssistRequest, NormalizationAssistResult, ProcessNodeRef, ProcessObservationRef
from soc_agent.contracts.normalization import (
    ContextObservationRef,
    DetectionObservationRef,
    NormalizationAdditionalFactProposal,
    NormalizationEventProposal,
    NormalizationObjectProposal,
    NormalizationObservationChange,
    NormalizationSource,
    SupplementaryFactRef,
)
from soc_agent.utils.hashing import stable_hash

OBJECT_FIELDS = {
    "file": ("file_name", "file_path", "md5", "sha1", "sha256", "exists"),
    "process": ("process_name", "process_path", "process_id", "command_line", "username", "md5", "sha256", "parent_process_name", "parent_process_id", "parent_command_line"),
    "network": ("source_ip", "destination_ip", "src_port", "dst_port", "protocol", "application_protocol", "direction", "domain", "url"),
    "http": ("method", "host", "path", "url", "protocol", "status_code", "user_agent"),
    "host": ("host_name", "host_id", "asset_id", "ip_address"),
    "user": ("username", "user_id", "um_account"),
    "container": ("container_id", "container_name", "image_id", "image_name", "namespace", "pod_name", "cluster_name"),
}
_HASH_LENGTHS = {"md5": 32, "sha1": 40, "sha256": 64}
_MAX_OBSERVATIONS = 40


def _get(root, path):
    value = root
    for name, index in re.findall(r"([\w]+)|\[(\d+)\]", path):
        value = value[int(index)] if index else value[name]
    return value


def _put(root, path, value):
    tokens = [int(index) if index else name for name, index in re.findall(r"([\w]+)|\[(\d+)\]", path)]
    parent = root
    for key in tokens[:-1]:
        parent = parent[key]
    last = tokens[-1]
    if isinstance(last, int) and last == len(parent):
        parent.append(value)
    else:
        parent[last] = value


def _scope_matches(path, source):
    return path == source or path.startswith(source + "#") or path.startswith(source + ".")


def build_object_catalog(alert: AlertInput, sources: list[NormalizationSource], *, omitted_paths: list[str] | None = None) -> dict[str, dict[str, Any]]:
    catalog = {}
    budget = 16_000

    def add(kind, source, path, obj):
        nonlocal budget
        attrs = {k: v for k, v in obj.model_dump(exclude_none=True).items() if k in OBJECT_FIELDS[kind]}
        size = len(json.dumps(attrs, ensure_ascii=False))
        if attrs and len(catalog) < 40 and size <= min(2500, budget):
            catalog[f"O{len(catalog)}"] = {"kind": kind, "source_id": source.source_id, "path": path, "attributes": attrs}
            budget -= size
        elif attrs and omitted_paths is not None:
            omitted_paths.append(path)

    for kind in ("file", "process", "network", "http"):
        if sources:
            add(kind, sources[0], f"entities.{kind}", getattr(alert.entities, kind))
        for i, observation in enumerate(getattr(alert.entities, kind).observations):
            source = next((s for s in sources if _scope_matches(observation.evidence_path, s.source_path)), None)
            if source is None:
                continue
            base = f"entities.{kind}.observations[{i}]"
            entries = [(base + f".nodes[{j}]", node) for j, node in enumerate(observation.nodes)] if kind == "process" else [(base, observation)]
            for path, obj in entries:
                add(kind, source, path, obj)
    return catalog


def _quote(proposal, request):
    sources = request.sources or [NormalizationSource(source_id="L0", source_path=request.source_path or "", text=request.source_text)]
    source = next((s for s in sources if s.source_id == proposal.source_id), None)
    if source is None:
        raise ValueError("unknown_source")
    if not request.reference_validation_enabled:
        return source, None
    start = proposal.quote_start
    if start is None:
        count = source.text.count(proposal.source_quote)
        if count != 1:
            raise ValueError("missing_quote" if count == 0 else "ambiguous_quote")
        start = source.text.index(proposal.source_quote)
    if source.text[start : start + len(proposal.source_quote)] != proposal.source_quote:
        raise ValueError("missing_quote")
    return source, start


def _check_values(values, quote, *, reference_validation_enabled=True):
    # Allow faithful JSON/HTML escaping, not free text correction of source values.
    decoded = html.unescape(quote).replace('\\"', '"')
    for value in values:
        text = str(value).lower() if isinstance(value, bool) else str(value)
        if len(text) > 8000 or "<ENCODED:" in text or "[REDACTED]" in text:
            raise ValueError("oversized_or_omitted_value")
        if reference_validation_enabled and text not in quote and text not in decoded and json.dumps(value, ensure_ascii=False).strip('"') not in quote:
            raise ValueError("value_not_in_quote")


def _validate_attributes(kind, attrs):
    if set(attrs) - set(OBJECT_FIELDS[kind]):
        raise ValueError("unsupported_attribute")
    for key, value in attrs.items():
        if key in _HASH_LENGTHS and not re.fullmatch(r"[a-fA-F0-9]{" + str(_HASH_LENGTHS[key]) + "}", str(value)):
            raise ValueError("invalid_hash")
        if key.endswith("ip") or key == "ip_address":
            ipaddress.ip_address(value)
        if key in {"src_port", "dst_port"} and (isinstance(value, bool) or not str(value).isdecimal() or not 1 <= int(value) <= 65535):
            raise ValueError("invalid_port")
        if key in {"process_id", "parent_process_id"} and (isinstance(value, bool) or not str(value).isdecimal()):
            raise ValueError("invalid_pid")
        if key == "method" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", str(value)):
            raise ValueError("invalid_http_method")
        if key in {"protocol", "application_protocol"} and not re.fullmatch(r"[A-Za-z][A-Za-z0-9./_-]{0,39}", str(value)):
            raise ValueError("invalid_protocol")


def _leaf(path):
    return (PureWindowsPath(path) if "\\" in path else PurePosixPath(path)).name


def _identity(kind, attrs):
    if kind == "file":
        return ("file_path", str(attrs["file_path"])) if attrs.get("file_path") else next(((k, str(attrs[k]).lower()) for k in _HASH_LENGTHS if attrs.get(k)), None)
    if kind == "process":
        if attrs.get("process_id") is not None and attrs.get("process_name"):
            return (str(attrs["process_id"]), str(attrs["process_name"]).casefold())
        return None
    if kind == "network" and all(attrs.get(k) is not None for k in ("source_ip", "destination_ip", "src_port", "dst_port", "protocol")):
        return tuple(str(attrs[k]) for k in ("source_ip", "destination_ip", "src_port", "dst_port", "protocol"))
    return None


def _object_record(proposal, source):
    attrs = dict(proposal.attributes)
    if proposal.kind in {"file", "process"}:
        path_key, name_key = ("file_path", "file_name") if proposal.kind == "file" else ("process_path", "process_name")
        if attrs.get(path_key) and not attrs.get(name_key):
            attrs[name_key] = _leaf(str(attrs[path_key]))
    base = {"observation_id": "SEM-" + stable_hash([source.source_path, proposal.kind, attrs])[:16], "evidence_path": source.source_path + "#semantic"}
    kind = proposal.kind
    if kind == "file":
        return FileObservationRef(**base, relation="observed_artifact", **attrs), attrs
    if kind == "process":
        node = ProcessNodeRef.model_validate({k: v for k, v in attrs.items() if k in ProcessNodeRef.model_fields})
        nodes = []
        if attrs.get("parent_process_name"):
            nodes.append(ProcessNodeRef(process_name=attrs["parent_process_name"], process_id=attrs.get("parent_process_id"), command_line=attrs.get("parent_command_line")))
        nodes.append(node)
        return ProcessObservationRef(**base, event_scope_id=source.source_path, nodes=nodes), attrs
    if kind == "network":
        return NetworkObservationRef(**base, **attrs), attrs
    if kind == "http":
        return HttpObservationRef(**base, **attrs), attrs
    return ContextObservationRef(**base, event_scope_id=source.source_path, kind=kind, attributes=attrs), attrs


def collect_observation_changes(alert: AlertInput, request: NormalizationAssistRequest, payload: dict[str, Any], report: NormalizationAssistResult) -> None:
    working = alert.model_dump(mode="json")
    refs = {}
    objects = payload.get("objects", [])
    counts = Counter(o["id"] for o in objects if isinstance(o, dict) and isinstance(o.get("id"), str))

    def write(path, record, proposal, source, start):
        try:
            before = _get(working, path)
        except IndexError:
            before = None
        after = record.model_dump(mode="json")
        if before == after:
            return
        change = NormalizationObservationChange(
            target=path,
            before=deepcopy(before),
            after=deepcopy(after),
            source_id=source.source_id,
            source_path=source.source_path,
            source_quote=proposal.source_quote,
            source_start=start,
            source_end=start + len(proposal.source_quote) if start is not None else None,
            reference_validation_status="verified" if request.reference_validation_enabled else "not_checked",
            reason="按原始日志核对对象、检测及其归属。",
            canonical_status="shadow" if report.mode == "shadow" else "applied",
        )
        candidate = json.loads(json.dumps(working))
        _put(candidate, path, after)
        AlertInput.model_validate(candidate)
        _put(working, path, after)
        report.observation_changes.append(change)

    for index, item in enumerate(objects):
        try:
            proposal = NormalizationObjectProposal.model_validate(item)
            if counts[proposal.id] != 1 or proposal.id in request.object_catalog:
                raise ValueError("duplicate_object_id")
            source, start = _quote(proposal, request)
            valid_attrs = {}
            for key, value in proposal.attributes.items():
                try:
                    _check_values([value], proposal.source_quote, reference_validation_enabled=request.reference_validation_enabled)
                    _validate_attributes(proposal.kind, {key: value})
                    valid_attrs[key] = value
                except (ValueError, TypeError) as exc:
                    report.issues.append(f"对象 {index + 1} 的 {key} 未采纳：{_error_code(exc)}。")
            if not valid_attrs:
                raise ValueError("no_valid_object_attributes")
            proposal = proposal.model_copy(update={"attributes": valid_attrs})
            kind = proposal.kind
            attrs = dict(valid_attrs)
            if kind in {"file", "process"}:
                path_key, name_key = ("file_path", "file_name") if kind == "file" else ("process_path", "process_name")
                if attrs.get(path_key) and not attrs.get(name_key):
                    attrs[name_key] = _leaf(str(attrs[path_key]))
            candidates = build_object_catalog(AlertInput.model_validate(working), [source])
            existing = request.object_catalog.get(proposal.existing_ref) if proposal.existing_ref else None
            if proposal.existing_ref and (existing is None or existing["kind"] != kind or existing["source_id"] != source.source_id):
                raise ValueError("invalid_existing_reference")
            if existing is None and (identity := _identity(kind, attrs)):
                matches = [c for c in candidates.values() if c["kind"] == kind and ".observations[" in c["path"] and _identity(kind, c["attributes"]) == identity]
                if len(matches) > 1:
                    raise ValueError("ambiguous_existing_object")
                existing = matches[0] if matches else None
            if existing:
                path = existing["path"]
                before = _get(working, path)
                identity_keys = ("file_path",) if kind == "file" else ("process_name", "process_path", "process_id") if kind == "process" else ()
                if any(before.get(k) is not None and k in attrs and before[k] != attrs[k] for k in identity_keys):
                    raise ValueError("object_identity_change_requires_distinct_observation")
                if any(before.get(k) and k in attrs and str(before[k]).lower() != str(attrs[k]).lower() for k in _HASH_LENGTHS):
                    raise ValueError("conflicting_hash_for_existing_object")
                if path == f"entities.{kind}":
                    record = type(getattr(alert.entities, kind)).model_validate({**before, **attrs})
                elif kind == "process":
                    if any(k.startswith("parent_") for k in attrs):
                        report.issues.append(f"对象 {index + 1} 的父进程修改未采纳：已有进程树需通过独立对象核对；其他属性继续合并。")
                        attrs = {k: v for k, v in attrs.items() if not k.startswith("parent_")}
                    record = ProcessNodeRef.model_validate({**before, **attrs})
                else:
                    record_type = {"file": FileObservationRef, "http": HttpObservationRef, "network": NetworkObservationRef}[kind]
                    record = record_type.model_validate({**before, **attrs})
            else:
                record, attrs = _object_record(proposal, source)
                collection = f"entities.{kind}.observations" if kind in {"file", "process", "network", "http"} else "entities.context_observations"
                records = _get(working, collection)
                same = next((i for i, r in enumerate(records) if r.get("observation_id") == record.observation_id), None)
                if same is None and len(records) >= _MAX_OBSERVATIONS:
                    raise ValueError("observation_budget")
                path = collection + f"[{same if same is not None else len(records)}]"
            write(path, record, proposal, source, start)
            refs[proposal.id] = (path, source.source_id)
            report.reviewed_fact_count += len(attrs)
        except (ValueError, ValidationError, TypeError, KeyError) as exc:
            report.issues.append(f"对象 {index + 1} 未采纳：{_error_code(exc)}；其他对象继续处理。")

    def subject(ref, source):
        candidate = refs.get(ref)
        if candidate is None:
            existing = request.object_catalog.get(ref)
            if existing:
                candidate = (existing["path"], existing["source_id"])
        if candidate is None or candidate[1] != source.source_id:
            raise ValueError("unknown_or_cross_event_subject")
        return candidate[0]

    for group, cls, target in (("events", NormalizationEventProposal, "detections"), ("additional_facts", NormalizationAdditionalFactProposal, "supplementary_facts")):
        for index, item in enumerate(payload.get(group, [])):
            try:
                proposal = cls.model_validate(item)
                source, start = _quote(proposal, request)
                if group == "events":
                    _check_values(
                        [v for v in (proposal.name, proposal.category, proposal.detector_id, proposal.reported_result, proposal.action) if v is not None],
                        proposal.source_quote,
                        reference_validation_enabled=request.reference_validation_enabled,
                    )
                    attrs = proposal.model_dump(exclude={"source_id", "source_quote", "quote_start"})
                    attrs["subject_refs"] = [subject(ref, source) for ref in proposal.subject_refs]
                    cls_out = DetectionObservationRef
                else:
                    _check_values([proposal.value], proposal.source_quote, reference_validation_enabled=request.reference_validation_enabled)
                    attrs = {"name": proposal.name, "value": proposal.value, "meaning": proposal.meaning, "subject_ref": subject(proposal.subject_ref, source) if proposal.subject_ref else None}
                    cls_out = SupplementaryFactRef
                record = cls_out(observation_id="SEM-" + stable_hash([source.source_path, attrs])[:16], evidence_path=source.source_path + "#semantic", event_scope_id=source.source_path, **attrs)
                records = working["entities"][target]
                same = next((i for i, r in enumerate(records) if r["observation_id"] == record.observation_id), None)
                if same is None and len(records) >= _MAX_OBSERVATIONS:
                    raise ValueError("observation_budget")
                write(f"entities.{target}[{same if same is not None else len(records)}]", record, proposal, source, start)
                report.reviewed_fact_count += 1
            except (ValueError, ValidationError, TypeError, KeyError) as exc:
                report.issues.append(f"{'检测记录' if group == 'events' else '补充事实'} {index + 1} 未采纳：{_error_code(exc)}。")


def _error_code(exc):
    return "字段类型或必填内容不符合约定" if isinstance(exc, ValidationError) else str(exc)[:100]


def apply_observation_changes(alert: AlertInput, request: NormalizationAssistRequest, report: NormalizationAssistResult) -> AlertInput:
    if report.mode != "apply" or report.status == "failed" or not report.observation_changes:
        return alert
    data = alert.model_dump(mode="json")
    provenance = data["extensions"].setdefault("canonical_field_provenance", [])
    for change in report.observation_changes:
        try:
            before = _get(data, change.target)
        except IndexError:
            before = None
        if before != change.before:
            raise ValueError("semantic_merge_snapshot_changed")
        # Nested updates must not mutate a previous proposal's frozen snapshot.
        _put(data, change.target, deepcopy(change.after))
        previous = dict(_scalars(change.before)) if change.before else {}
        for suffix, value in _scalars(change.after):
            # Empty source values remain in canonical/audit data, not scalar provenance.
            if value == "":
                continue
            if suffix in previous and previous[suffix] == value:
                continue
            if suffix.rsplit(".", 1)[-1] in {"observation_id", "evidence_path", "event_scope_id"}:
                continue
            provenance.append(
                CanonicalFieldProvenance(
                    canonical_path=change.target + suffix,
                    selected_value=str(value),
                    selected_from=f"{change.source_path}#semantic[{change.source_start}:{change.source_end}]" if change.reference_validation_status == "verified" else f"{change.source_path}#semantic-unverified",
                    source_layer=request.source_layer,
                    trust_level=request.source_trust,
                    selection_reason="llm_semantic_review: " + change.reason,
                ).model_dump(mode="json")
            )
    return AlertInput.model_validate(data)


def _scalars(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _scalars(item, path + "." + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _scalars(item, path + f"[{index}]")
    elif value is not None:
        yield path, value


def observation_projection_present(projected: dict[str, Any], change: NormalizationObservationChange) -> bool:
    path = change.target.replace("entities.", "canonical_entities.", 1)
    try:
        actual = _get(projected, path)
    except (KeyError, IndexError, TypeError):
        return False
    try:
        return all(_get(actual, suffix.lstrip(".")) == value for suffix, value in _scalars(change.after))
    except (KeyError, IndexError, TypeError):
        return False
