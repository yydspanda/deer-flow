"""Project verified process subjects and command intent without inventing traffic.

Only the new profile consumes this module. Saved profiles retain their original
summary-based projection. Node identity and source ownership remain canonical;
neither raw fields nor shell commands are reparsed in Memory.
"""

import ntpath
import re
from urllib.parse import quote

from soc_agent.contracts import LLMAnalysisRequest


def _belongs(path: str, source: str) -> bool:
    return path == source or path.startswith(source + "#") or path.startswith(source + ".")


def has_process_facts(request: LLMAnalysisRequest) -> bool:
    return any(observation.subject_binding is not None for observation in request.canonical_entities.process.observations) or any(
        fact.process_behavior is not None and fact.process_behavior_verification == "source_bound_v1" for fact in request.canonical_entities.supplementary_facts
    )


def _leaf(value: str | None) -> str | None:
    return ntpath.basename(value.strip().replace("/", "\\")).casefold() or None if value and value.strip() else None


def _path(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    _, suffix = ntpath.splitdrive(ntpath.normpath(value.strip().replace("/", "\\")).casefold())
    suffix = suffix.lstrip("\\").replace("\\", "/")
    return suffix if suffix and suffix != "." else None


def bound_processes(request: LLMAnalysisRequest):
    """Resolve source-bound subjects, returning explicit gaps for broken bindings."""
    subjects, gaps = {}, []
    for index, observation in enumerate(request.canonical_entities.process.observations):
        binding = observation.subject_binding
        if binding is None:
            continue
        prefix = f"entities.process.observations[{index}]"
        gaps.extend(f"{prefix}:{gap}" for gap in binding.gaps)
        source = observation.event_scope_id
        if not source or not _belongs(observation.evidence_path, source) or any(not _belongs(path, source) for path in binding.evidence_paths):
            gaps.append(prefix + ":process_subject_source_mismatch")
            continue
        if binding.status != "resolved":
            gaps.append(prefix + ":process_subject_" + binding.status)
            continue
        position = binding.node_index
        if position is None or not 0 <= position < len(observation.nodes):
            gaps.append(prefix + ":process_subject_not_found")
            continue
        node = observation.nodes[position]
        parent = None
        if binding.parent_node_index is not None:
            if not 0 <= binding.parent_node_index < len(observation.nodes) or binding.parent_node_index == position:
                gaps.append(prefix + ":process_parent_not_found")
            else:
                parent = observation.nodes[binding.parent_node_index]
        for relative, evidence_path in observation.field_sources.items():
            match = re.fullmatch(r"nodes\[(\d+)\]\.([a-z_]+)", relative)
            if not match or int(match[1]) not in {position, binding.parent_node_index}:
                continue
            if int(match[1]) >= len(observation.nodes):
                gaps.append(prefix + ":process_field_not_projected:" + relative)
                continue
            field = match[2]
            value = getattr(observation.nodes[int(match[1])], field, None)
            if not _belongs(evidence_path, source):
                gaps.append(prefix + ":process_field_source_mismatch:" + relative)
            elif value is None or value == "":
                gaps.append(prefix + ":process_field_not_projected:" + relative)
        subjects[f"{prefix}.nodes[{position}]"] = (source, node, parent)
    return subjects, gaps


def _verified_targets(request, subjects):
    gaps, errors = [], {}
    targets: dict[str, set[str]] = {}
    for index, fact in enumerate(request.canonical_entities.supplementary_facts):
        descriptor = fact.process_behavior
        if descriptor is None or fact.process_behavior_verification != "source_bound_v1":
            continue
        subject = subjects.get(fact.subject_ref or "")
        if subject is None:
            errors[index] = "process_command_target_subject_not_resolved:" + fact.observation_id
            gaps.append(errors[index])
            continue
        source, node, _ = subject
        if fact.event_scope_id != source or not _belongs(fact.evidence_path, source):
            errors[index] = "process_command_target_source_mismatch:" + fact.observation_id
            gaps.append(errors[index])
            continue
        binding = re.fullmatch(r"entities\.process\.observations\[(\d+)\]\.nodes\[(\d+)\]", fact.subject_ref)
        observation = request.canonical_entities.process.observations[int(binding[1])]
        command_source = observation.field_sources.get(f"nodes[{binding[2]}].command_line")
        if fact.evidence_path != command_source or fact.value != node.command_line:
            errors[index] = "process_command_target_command_mismatch:" + fact.observation_id
            gaps.append(errors[index])
            continue
        targets.setdefault(fact.subject_ref, set()).add(f"{descriptor.protocol}/{descriptor.dst_port}")
    return targets, gaps, errors


def _subject_components(node, parent, targets):
    components, strong = set(), set()
    image = _leaf(node.process_path)
    name = image or _leaf(node.process_name)
    path = _path(node.process_path)
    if name:
        components.update({"process:" + name, "observed_process:" + name})
    if image:
        components.add("process_image:" + image)
    if path:
        components.add("process_path:" + path)
    parent_name = _leaf(parent.process_path or parent.process_name) if parent is not None else None
    if parent_name:
        components.add("process:" + parent_name)
        if name:
            components.add(f"observed_process_edge:{parent_name}>{name}")
    if targets and name:
        parts = ["image=" + quote(name, safe="/._-")]
        if path:
            parts.append("path=" + quote(path, safe="/._-"))
        parts.append("command_target=" + ",".join(sorted(targets)))
        component = "process_command_target:" + ";".join(parts)
        components.add(component)
        strong.add(component)
    return components, strong


def _process_detection_components(request, subjects):
    """Retain each detector while preferring its source-declared duplicate."""
    observations = request.canonical_entities.process.observations
    managed = {index for index, item in enumerate(observations) if item.subject_binding is not None}
    candidates, gaps, usage = [], [], []
    for index, detection in enumerate(request.canonical_entities.detections):
        for original_ref in detection.subject_refs:
            match = re.fullmatch(r"entities\.process\.observations\[(\d+)\](?:\.nodes\[(\d+)\])?", original_ref)
            if not (match and int(match[1]) in managed) and original_ref != "entities.process":
                continue
            ref = original_ref
            if match and match[2] is None:
                binding = observations[int(match[1])].subject_binding
                ref += f".nodes[{binding.node_index}]"
            elif ref == "entities.process":
                ref = request.canonical_entities.process.primary_ref or ""
            subject = subjects.get(ref)
            error = None
            if subject is None:
                error = "process_detection_subject_not_resolved"
            elif detection.event_scope_id != subject[0] or not _belongs(detection.evidence_path, subject[0]):
                error = "process_detection_source_mismatch"
            elif detection.identity_basis == "ambiguous":
                error = "detector_identity_ambiguous"
            if error:
                gap = f"entities.detections[{index}]:{error}"
                gaps.append(gap)
                usage.append({"canonical_path": f"entities.detections[{index}]", "status": "gap", "components": [], "reason": gap})
                continue
            source, node, _ = subject
            adapter = detection.evidence_path == source + "#parsed" or detection.evidence_path.startswith(source + "#parsed.")
            candidates.append((index, ref, node, detection, adapter))

    def same_detector(left, right):
        if left.detector_id and right.detector_id:
            return left.detector_id == right.detector_id
        return " ".join(left.name.split()).casefold() == " ".join(right.name.split()).casefold()

    components = set()
    for index, ref, node, detection, adapter in candidates:
        declared = [item for item in candidates if item[4] and item[1] == ref and item[3].event_scope_id == detection.event_scope_id and same_detector(item[3], detection)]
        if declared and not adapter:
            usage.append({"canonical_path": f"entities.detections[{index}]", "status": "context_only", "components": [], "reason": "duplicate_of_source_declared_process_detection"})
            continue
        leaf = _leaf(node.process_path or node.process_name)
        if not leaf:
            gap = f"entities.detections[{index}]:process_detection_identity_missing"
            gaps.append(gap)
            usage.append({"canonical_path": f"entities.detections[{index}]", "status": "gap", "components": [], "reason": gap})
            continue
        namespace = (request.source.source_system or "unknown") + ":" + (request.source.product or "unknown")
        # A name/category remains part of this version's detector scope even
        # when its provider ID is shared by differently labelled detections.
        detector = (("id:" + detection.detector_id + ":") if detection.detector_id else "") + (detection.category or "") + ":" + detection.name
        identity = re.sub(r"\s+", "_", f"process:{namespace}:{detector}".casefold())
        component = f"detected_behavior:{identity}@process:{leaf}"
        components.add(component)
        usage.append({"canonical_path": f"entities.detections[{index}]", "status": "used", "components": [component], "reason": "source_bound_process_detector"})
    return components, gaps, usage


def process_behavior_components(request: LLMAnalysisRequest) -> tuple[list[str], list[str], list[str]]:
    subjects, gaps = bound_processes(request)
    targets, target_gaps, _ = _verified_targets(request, subjects)
    gaps.extend(target_gaps)
    components, strong = set(), set()
    for ref, (_, node, parent) in subjects.items():
        values, anchors = _subject_components(node, parent, targets.get(ref, set()))
        components.update(values)
        strong.update(anchors)
    detector_components, detector_gaps, _ = _process_detection_components(request, subjects)
    components.update(detector_components)
    strong.update(detector_components)
    gaps.extend(detector_gaps)
    return sorted(components), sorted(strong), sorted(set(gaps))


def process_projection_usage(request: LLMAnalysisRequest, *, node_components: dict[str, list[str]], core_components: list[str]) -> list[dict]:
    """Explain actual consumption without putting identities into match values."""
    subjects, gaps = bound_processes(request)
    targets, _, target_errors = _verified_targets(request, subjects)
    core = set(core_components)
    usage = []

    def add(path, status, reason, components=()):
        usage.append({"canonical_path": path, "status": status, "components": sorted(set(components) & core), "reason": reason})

    for index, observation in enumerate(request.canonical_entities.process.observations):
        if observation.subject_binding is None:
            continue
        prefix = f"entities.process.observations[{index}]"
        binding = observation.subject_binding
        selected_ref = f"{prefix}.nodes[{binding.node_index}]"
        own_gaps = [gap for gap in gaps if gap.startswith(prefix + ":")]
        add(prefix + ".subject_binding", "gap" if own_gaps else "used", "; ".join(own_gaps) if own_gaps else "source_bound_process_subject")
        for position, node in enumerate(observation.nodes):
            ref = f"{prefix}.nodes[{position}]"
            if ref in subjects:
                _, selected, parent = subjects[ref]
                components, _ = _subject_components(selected, parent, targets.get(ref, set()))
                components.update(node_components.get(ref, []))
                add(ref, "used" if components & core else "gap", "bound_process_components" if components & core else "bound_process_not_projected", components)
                for field in ("process_id", "username"):
                    if getattr(node, field) is not None:
                        add(ref + "." + field, "context_only", "occurrence_identity_excluded_from_behavior_matching")
            elif position == binding.parent_node_index and selected_ref in subjects:
                _, selected, parent = subjects[selected_ref]
                components, _ = _subject_components(selected, parent, set())
                parent_name = _leaf(node.process_path or node.process_name)
                components = {value for value in components if value.startswith("observed_process_edge:") or value == "process:" + str(parent_name)}
                add(ref, "used", "bound_parent_process_components", components)
                if node.process_id is not None:
                    add(ref + ".process_id", "context_only", "occurrence_identity_excluded_from_behavior_matching")
            else:
                add(ref, "context_only", "ancestor_or_unresolved_subject_not_used_as_execution_identity")
        for gap in own_gaps:
            if ":process_field_not_projected:" in gap or ":process_field_source_mismatch:" in gap:
                relative = gap.rsplit(":", 1)[1]
                add(prefix + "." + relative, "gap", gap)

    for index, fact in enumerate(request.canonical_entities.supplementary_facts):
        descriptor = fact.process_behavior
        if descriptor is None:
            continue
        prefix = f"entities.supplementary_facts[{index}]"
        if fact.process_behavior_verification != "source_bound_v1":
            add(prefix + ".process_behavior", "context_only", "unverified_command_target")
            continue
        if index in target_errors:
            add(prefix + ".process_behavior", "gap", target_errors[index])
        else:
            _, node, parent = subjects[fact.subject_ref]
            components, _ = _subject_components(node, parent, targets[fact.subject_ref])
            components = {value for value in components if value.startswith("process_command_target:")}
            add(prefix + ".process_behavior", "used", "source_bound_command_target_not_observed_network_service", components)
        add(prefix + ".process_behavior.destination_ip", "context_only", "command_destination_identity_excluded_from_behavior_matching")
    for item in _process_detection_components(request, subjects)[2]:
        add(item["canonical_path"], item["status"], item["reason"], item["components"])
    return usage


def process_semantic_request(request: LLMAnalysisRequest) -> LLMAnalysisRequest:
    """Avoid projecting a model's second copy of an adapter-owned process fact."""
    process = request.canonical_entities.process
    managed = {index: observation.event_scope_id for index, observation in enumerate(process.observations) if observation.subject_binding is not None}
    if not managed:
        return request
    observations = [observation.model_copy(update={"nodes": []}) if index in managed else observation for index, observation in enumerate(process.observations)]
    detections = []
    for detection in request.canonical_entities.detections:
        refs = []
        for ref in detection.subject_refs:
            match = re.fullmatch(r"entities\.process\.observations\[(\d+)\](?:\.nodes\[\d+\])?", ref)
            owned_process = (match and int(match[1]) in managed) or ref == "entities.process"
            command_network = detection.kind == "process_execution" and ref.startswith("entities.network") and detection.event_scope_id in managed.values()
            if not owned_process and not command_network:
                refs.append(ref)
        if refs or not detection.subject_refs or detection.identity_basis == "ambiguous":
            detections.append(detection.model_copy(update={"subject_refs": refs}))
    entities = request.canonical_entities.model_copy(update={"process": process.model_copy(update={"observations": observations}), "detections": detections})
    return request.model_copy(update={"canonical_entities": entities})
