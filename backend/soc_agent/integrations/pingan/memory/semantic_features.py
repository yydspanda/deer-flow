"""Versioned semantic features from canonical observations and bound subjects."""

import re
from pathlib import PurePosixPath, PureWindowsPath

from soc_agent.contracts import LLMAnalysisRequest
from soc_agent.memory.facets import network_service_from_direction

SEMANTIC_PREFIXES = ("detected_behavior:", "detected_file:", "target_port:", "web_detection_target", "observed_process:", "observed_process_edge:")


def valid_component(value: str) -> bool:
    if value.startswith(("protocol:", "http_method:")):
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9./_-]{0,39}", value.split(":", 1)[1]))
    return True


def _leaf(value):
    if not value:
        return None
    return (PureWindowsPath(value) if "\\" in value else PurePosixPath(value)).name.casefold()


def _subject(entities, path):
    if not re.fullmatch(r"entities\.(file|process|network|http)(?:\.observations\[\d+\](?:\.nodes\[\d+\])?)?", path):
        return None
    current = {"entities": entities.model_dump(mode="json")}
    try:
        for name, index in re.findall(r"([\w]+)|\[(\d+)\]", path):
            current = current[int(index)] if index else current[name]
        return current
    except (KeyError, IndexError, TypeError):
        return None


def _directional_service(subject):
    return network_service_from_direction(protocol=subject.get("protocol"), direction=subject.get("direction"), src_port=subject.get("src_port"), dst_port=subject.get("dst_port"))


def _network_subject_ambiguous(subject):
    observations = subject.get("observations", [])
    if not observations:
        return False
    connections = set()
    for item in [subject, *observations]:
        service = _directional_service(item)
        source, destination = item.get("source_ip"), item.get("destination_ip")
        if not service or not source or not destination or not item.get("src_port") or not item.get("dst_port"):
            return True
        # A request and its response are the same connection. A different or
        # unresolved endpoint/role cannot inherit the aggregate's service.
        endpoints = ((source, item["src_port"]), (destination, item["dst_port"]))
        server, client = endpoints if item["direction"].strip().casefold() == "to_client" else reversed(endpoints)
        connections.add((service.split("/", 1)[0], server, client))
    return len(connections) > 1


def semantic_behavior_components(request: LLMAnalysisRequest, *, stable: bool = False, directional_services: bool = False) -> tuple[list[str], list[str]]:
    entities = request.canonical_entities
    components, strong = set(), set()
    for detection in entities.detections:
        if detection.identity_basis == "ambiguous":
            continue
        subjects = [(ref.split(".")[1], _subject(entities, ref)) for ref in detection.subject_refs]
        subjects = [(kind, s) for kind, s in subjects if s is not None]
        if not subjects:
            continue
        label = ":".join([detection.kind, (detection.category or "").casefold(), detection.name.casefold()])
        label = re.sub(r"\s+", "_", label)[:512]
        for subject_kind, subject in subjects:
            binding = None
            kind = {"file": "file_detection", "network": "network_access", "process": "process_execution", "http": "web_detection"}.get(subject_kind, detection.kind) if stable else detection.kind
            if kind == "file_detection" and (leaf := _leaf(subject.get("file_path") or subject.get("file_name"))):
                components.add("detected_file:" + leaf)
                binding = "file:" + leaf
            elif kind == "network_access":
                if directional_services:
                    service = _directional_service(subject)
                    if service and not _network_subject_ambiguous(subject):
                        binding = "service:" + service
                elif stable and subject.get("dst_port"):
                    binding = "service:" + str(subject.get("protocol") or "unknown").casefold() + "/" + str(subject["dst_port"])
                elif subject.get("dst_port"):
                    components.add("target_port:" + str(subject["dst_port"]))
                    binding = "port:" + str(subject["dst_port"])
            elif kind == "process_execution" and (subject.get("nodes") or subject.get("process_name")):
                node = subject["nodes"][-1] if subject.get("nodes") else subject
                binding = "process:" + (_leaf(node.get("process_path") or node.get("process_name")) or "unknown")
            elif kind == "web_detection" and subject.get("host"):
                components.add("web_detection_target")
                binding = "http:" + str(subject.get("method") or "unknown").casefold()
            if binding:
                identity = label
                if stable:
                    # Vendor IDs are scoped by product/source. Without one keep
                    # the original label; do not invent cross-language aliases.
                    source = getattr(request, "source", None)
                    namespace = str(getattr(source, "source_system", None) or "unknown") + ":" + str(getattr(source, "product", None) or "unknown")
                    detector = "id:" + detection.detector_id if detection.detector_id else (detection.category or "") + ":" + detection.name
                    if subject_kind == "file" and detection.detector_id:
                        detector += ":" + (detection.category or "") + ":" + detection.name
                    identity = re.sub(r"\s+", "_", f"{subject_kind}:{namespace}:{detector}".casefold())
                anchor = "detected_behavior:" + identity + "@" + binding
                components.add(anchor)
                strong.add(anchor)
    # Each observation retains its own ancestry; never join different trees here.
    for observation in entities.process.observations:
        if not observation.nodes:
            continue
        node = observation.nodes[-1]
        leaf = _leaf(node.process_path or node.process_name)
        if leaf and leaf not in {"system", "unknown"}:
            components.add("observed_process:" + leaf)
        if len(observation.nodes) >= 2:
            parent = _leaf(observation.nodes[-2].process_path or observation.nodes[-2].process_name)
            if parent and leaf:
                components.add(f"observed_process_edge:{parent}>{leaf}")
    return sorted(components), sorted(strong)


def semantic_projection_gaps(request: LLMAnalysisRequest, *, directional_services: bool = False) -> list[str]:
    gaps = []
    for index, detection in enumerate(request.canonical_entities.detections):
        if detection.identity_basis == "ambiguous":
            gaps.append(f"entities.detections[{index}]:detector_identity_ambiguous")
        for ref in detection.subject_refs or [""]:
            subject = _subject(request.canonical_entities, ref)
            kind = ref.split(".")[1] if "." in ref else None
            if directional_services and kind == "network" and subject and _network_subject_ambiguous(subject):
                gaps.append(f"entities.detections[{index}]:network_subject_ambiguous:{ref}")
                continue
            supported = subject and (
                (kind == "file" and (subject.get("file_path") or subject.get("file_name")))
                or (kind == "network" and (_directional_service(subject) if directional_services else subject.get("dst_port")))
                or (kind == "process" and (subject.get("nodes") or subject.get("process_name")))
                or (kind == "http" and subject.get("host"))
            )
            if not supported:
                gaps.append(f"entities.detections[{index}]:subject_not_projected:{ref or 'unbound'}")
    return gaps
