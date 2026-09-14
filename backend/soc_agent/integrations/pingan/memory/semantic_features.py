"""Opt-in v6 features from canonical observations, never vendor raw aliases."""

import re
from pathlib import PurePosixPath, PureWindowsPath

from soc_agent.contracts import LLMAnalysisRequest

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


def semantic_behavior_components(request: LLMAnalysisRequest) -> tuple[list[str], list[str]]:
    entities = request.canonical_entities
    components, strong = set(), set()
    for detection in entities.detections:
        subjects = [_subject(entities, ref) for ref in detection.subject_refs]
        subjects = [s for s in subjects if s is not None]
        if not subjects:
            continue
        label = ":".join([detection.kind, (detection.category or "").casefold(), detection.name.casefold()])
        label = re.sub(r"\s+", "_", label)[:512]
        for subject in subjects:
            binding = None
            if detection.kind == "file_detection" and (leaf := _leaf(subject.get("file_path") or subject.get("file_name"))):
                components.add("detected_file:" + leaf)
                binding = "file:" + leaf
            elif detection.kind == "network_access" and subject.get("dst_port"):
                components.add("target_port:" + str(subject["dst_port"]))
                binding = "port:" + str(subject["dst_port"])
            elif detection.kind == "process_execution" and (subject.get("nodes") or subject.get("process_name")):
                node = subject["nodes"][-1] if subject.get("nodes") else subject
                binding = "process:" + (_leaf(node.get("process_path") or node.get("process_name")) or "unknown")
            elif detection.kind == "web_detection" and subject.get("host"):
                components.add("web_detection_target")
                binding = "http:" + str(subject.get("method") or "unknown").casefold()
            if binding:
                anchor = "detected_behavior:" + label + "@" + binding
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
