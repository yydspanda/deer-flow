"""Remove retired model registry entries without copying credentials into a patch."""

import ast
import re

RETIRED = ("DeepSeek_V32", "DeepSeek_V4_Flash_My")
YAML_PATHS = (
    "llm_service/litellm_proxy_config.local.yaml",
    "llm_service/litellm_proxy_config.prd.yaml",
)
SUPPORTED_PATHS = (*YAML_PATHS, "llm_service/openai_completion.py", "run.sh")

# This standalone standard-library code runs only in the existing PRD branch.
# Tests execute it against temporary configuration files, never via run.sh.
PRD_SUPERVISOR_SELECTOR = r'''import configparser
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


PUB = "Pub_DeepSeek_V4_Flash"
WORKER = "program:" + PUB + "_worker"
RETIRED = {"program:DeepSeek_V32_worker", "program:DeepSeek_V4_Flash_My_worker"}
NORMAL = "celery -A tasks.task.app worker -c {capacity} --loglevel=info -Q " + PUB + "_queue -n " + PUB + "_worker_%%h"
DETECTOR = "python scripts/detector_worker.py pub -- -A tasks.task.app worker -c {capacity} --loglevel=info -Q " + PUB + "_queue -n " + PUB + "_worker_%%h"


def parse(source):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(source)
    return config


def normalized(source):
    markers = list(re.finditer(r"^\[([^\]\r\n]+)\].*$", source, re.MULTILINE))
    if not markers:
        raise ValueError("missing sections")
    blocks = [source[:markers[0].start()]]
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(source)
        name = marker.group(1)
        block = source[marker.start():end]
        if name in RETIRED:
            continue
        if name == WORKER:
            block = block.replace("worker -c 15 ", "worker -c 27 ", 1)
        blocks.append(block)
    return "".join(blocks)


def main():
    active, normal, detector = map(Path, sys.argv[1:])
    source = active.read_bytes().decode("utf-8")
    command = parse(source)[WORKER]["command"]
    modes = [(NORMAL, normal), (DETECTOR, detector)]
    selected = [(template, path) for template, path in modes
                if command in {template.format(capacity=15), template.format(capacity=27)}]
    if len(selected) != 1:
        raise ValueError("unrecognized Pub worker command")
    template, candidate = selected[0]
    target_bytes = candidate.read_bytes()
    target = target_bytes.decode("utf-8")
    config = parse(target)
    if config[WORKER]["command"] != template.format(capacity=27) or RETIRED.intersection(config.sections()):
        raise ValueError("invalid PRD worker configuration")
    if normalized(source) != target:
        raise ValueError("non-target Supervisor configuration differs")
    if source == target:
        return
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=active.parent, prefix=".pub-worker-", delete=False) as handle:
            pending = Path(handle.name)
            os.fchmod(handle.fileno(), stat.S_IMODE(active.stat().st_mode))
            handle.write(target_bytes)
        os.replace(pending, active)
        pending = None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


try:
    main()
except Exception:
    print("[ERROR] PRD worker config selection failed; verify the installed Supervisor config and both derived PRD files.", file=sys.stderr)
    sys.exit(1)
'''

PRD_SUPERVISOR_SELECTION = (
    "\n  # PRD 独立配置保留当前 normal/Detector 模式；STG 分支保持原样。\n"
    "  python - /etc/supervisor/conf.d/celery_task.conf config/celery_task.prd.pub.conf config/celery_task.detector.prd.pub.conf <<'SOC_PUB_SUPERVISOR' || exit 1\n"
    + PRD_SUPERVISOR_SELECTOR
    + "SOC_PUB_SUPERVISOR\n"
)


def remove_ranges(source, ranges):
    lines = source.splitlines(keepends=True)
    for first, last in sorted(ranges, reverse=True):
        del lines[first:last]
    return "".join(lines)


def adapter(source):
    candidates = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(
            isinstance(t, ast.Name) and t.id == "PRD_MODEL_CONFIGS" for t in targets
        ):
            candidates.append(node.value)
    if len(candidates) != 1 or not isinstance(candidates[0], ast.Dict):
        raise ValueError("unexpected_prd_model_registry")
    entries = candidates[0]
    ranges = []
    found = set()
    for key, value in zip(entries.keys, entries.values):
        if isinstance(key, ast.Constant) and key.value in RETIRED:
            found.add(key.value)
            ranges.append((key.lineno - 1, value.end_lineno))
    if found != set(RETIRED):
        raise ValueError("unexpected_retired_adapter_entries")
    result = remove_ranges(source, ranges)
    ast.parse(result)
    return result


def yaml_model_blocks(source):
    lines = source.splitlines(keepends=True)
    entries = {}
    for first, line in enumerate(lines):
        match = re.fullmatch(r"  - model_name: ([A-Za-z0-9_]+)\s*", line)
        if match is None:
            continue
        last = first + 1
        while last < len(lines):
            next_line = lines[last]
            if next_line.startswith("  - model_name:"):
                break
            if (
                next_line.strip()
                and not next_line[0].isspace()
                and not next_line.startswith("#")
            ):
                break
            last += 1
        name = match.group(1)
        if name in entries:
            raise ValueError("duplicate_model_registration")
        entries[name] = (first, last, "".join(lines[first:last]))
    return entries


def proxy_yaml(source):
    entries = yaml_model_blocks(source)
    if not set(RETIRED).issubset(entries) or "Pub_DeepSeek_V4_Flash" not in entries:
        raise ValueError("unexpected_proxy_model_registry")
    return remove_ranges(source, [entries[name][:2] for name in RETIRED])


def launcher(source):
    """Select the PRD-only Supervisor file without editing the STG branch."""
    marker = 'else\n  export CELERY_LOG_PATH="/mnt/nas_prd/glm/nas/log"\n'
    if (
        source.count(marker) != 1
        or source.count('if [ "$env_profile" = "stg" ]; then\n') != 1
        or source.count("supervisord -n -c /etc/supervisor/supervisord.conf") != 1
    ):
        raise ValueError("unexpected_production_launch_branch")
    if "config/celery_task.prd.pub.conf" in source:
        raise ValueError("production_supervisor_selection_already_present")
    return source.replace(marker, marker + PRD_SUPERVISOR_SELECTION, 1)


def transforms(path, source):
    if path in YAML_PATHS:
        return proxy_yaml(source)
    if path == "llm_service/openai_completion.py":
        return adapter(source)
    if path == "run.sh":
        return launcher(source)
    raise ValueError("unsupported_proxy_path:" + path)
