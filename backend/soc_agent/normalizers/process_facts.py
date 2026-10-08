"""Derive the compatibility process summary from its explicit canonical subject."""

from __future__ import annotations

import re

from soc_agent.contracts import AlertInput, CanonicalFieldProvenance, EvidenceInputPolicy

PROCESS_NODE_REF = re.compile(r"entities\.process\.observations\[(\d+)\]\.nodes\[(\d+)\]")
_SUMMARY_FIELDS = ("process_name", "process_id", "process_path", "command_line", "md5", "sha256")
_PARENT_FIELDS = {"parent_process_name": "process_name", "parent_process_id": "process_id", "parent_command_line": "command_line"}


def bound_primary_process(data: dict, *, source_path: str | None = None):
    """Resolve only an adapter-bound subject; never infer from names or list order."""
    process = data["entities"]["process"]
    ref = process.get("primary_ref")
    match = PROCESS_NODE_REF.fullmatch(ref or "")
    if not match:
        raise ValueError("process_primary_binding_invalid")
    observation_index, node_index = map(int, match.groups())
    try:
        observation = process["observations"][observation_index]
        binding = observation.get("subject_binding") or {}
        if binding.get("status") != "resolved" or binding.get("node_index") != node_index:
            raise ValueError("process_primary_binding_mismatch")
        evidence = observation["evidence_path"]
        if source_path is not None and not (evidence == source_path or evidence.startswith(source_path + "#") or evidence.startswith(source_path + ".")):
            raise ValueError("unknown_or_cross_event_subject")
        node = observation["nodes"][node_index]
        parent_index = binding.get("parent_node_index")
        if parent_index is not None and (parent_index == node_index or parent_index < 0):
            raise ValueError("process_primary_binding_parent_invalid")
        parent = observation["nodes"][parent_index] if parent_index is not None else None
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError("process_primary_binding_invalid") from exc
    return ref, observation, node, parent


def has_process_binding(data: dict) -> bool:
    process = data["entities"]["process"]
    return bool(process.get("primary_ref") or any(item.get("subject_binding") is not None for item in process.get("observations", [])))


def project_primary_process(alert: AlertInput) -> AlertInput:
    """Return an immutable, idempotent summary projection with deterministic lineage."""
    if not alert.entities.process.primary_ref:
        return alert
    data = alert.model_dump(mode="json")
    ref, observation, node, parent = bound_primary_process(data)
    process = data["entities"]["process"]
    parent_index = observation["subject_binding"].get("parent_node_index")
    sources = {key: (node.get(key), ref + "." + key) for key in _SUMMARY_FIELDS}
    for key, parent_key in _PARENT_FIELDS.items():
        sources[key] = (parent.get(parent_key) if parent else None, ref.rsplit(".nodes[", 1)[0] + f".nodes[{parent_index}].{parent_key}")
    if parent is None and observation.get("parent_process_id") is not None:
        sources["parent_process_id"] = (observation["parent_process_id"], ref.rsplit(".nodes[", 1)[0] + ".parent_process_id")
    try:
        policy = EvidenceInputPolicy.model_validate(alert.extensions.get("evidence_input_policy", {}))
        layer, trust = policy.selected_layer, policy.trust_level
    except ValueError:
        layer, trust = "raw_message", "unknown"
    provenance = data["extensions"].setdefault("canonical_field_provenance", [])
    node_provenance = {source: [item for item in provenance if item.get("canonical_path") == source] for _, source in sources.values()}
    targets = {"entities.process." + key for key in sources}
    # These summary fields are a view, not independent evidence or a second vote.
    provenance[:] = [item for item in provenance if item.get("canonical_path") not in targets]
    for key, (value, source) in sources.items():
        process[key] = value
        if value is None or value == "":
            continue
        matching = next((item for item in reversed(node_provenance[source]) if item.get("selected_value") == str(value)), None)
        relative = source.removeprefix(ref.rsplit(".nodes[", 1)[0] + ".")
        direct = observation.get("field_sources", {}).get(relative)
        provenance.append(
            CanonicalFieldProvenance(
                canonical_path="entities.process." + key,
                selected_value=str(value),
                selected_from=direct or (matching["selected_from"] if matching else source),
                source_layer=matching.get("source_layer", layer) if matching else layer,
                trust_level=matching.get("trust_level", trust) if matching else trust,
                selection_reason=f"deterministic_process_summary: derived from {source} using primary_ref and subject_binding",
            ).model_dump(mode="json")
        )
    return AlertInput.model_validate(data)
