"""Bounded edits for reviewed legacy launchers and operational utilities.

The installer owns exact source/target hashes and backups. These transforms are
deliberately strict about expected snippets and never execute target code. No
private complete configuration, credentials, or generated source is embedded.
"""

from __future__ import annotations

import ast
import re


PUB = "Pub_DeepSeek_V4_Flash"
RETIRED = ("DeepSeek_V32", "DeepSeek_V4_Flash_My")
LOCAL_CAPACITY_COMMENT = "# LOCAL 保持 Pub 并发 2；生产 Supervisor 配置为 27。"
SUPPORTED_PATHS = frozenset(
    {
        "config/celery_task.conf",
        "config/celery_task.detector.conf",
        "local_run.sh",
        "local_run.detector.sh",
        "my_workflows/util/check_celery_queue.sh",
        "validation/cleanup_priority9_tasks.py",
    }
)


def _once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError("expected exactly one reviewed source fragment")
    return source.replace(old, new, 1)


def _parse(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError:
        raise ValueError("Python source syntax differs from reviewed input") from None


def _program_span(source: str, model: str) -> tuple[int, int]:
    markers = list(re.finditer(r"^\[[^\]\r\n]+\].*$", source, re.MULTILINE))
    matching = [
        index
        for index, marker in enumerate(markers)
        if marker.group().strip() == f"[program:{model}_worker]"
    ]
    if len(matching) != 1:
        raise ValueError("expected exactly one reviewed worker section")
    index = matching[0]
    return markers[index].start(), markers[index + 1].start() if index + 1 < len(
        markers
    ) else len(source)


def _supervisor(source: str) -> str:
    """Derive a PRD-only file; the installer must retain its STG source file."""
    for model in RETIRED:
        start, end = _program_span(source, model)
        section = source[start:end]
        if (
            section.count(f"-Q {model}_queue ") != 1
            or section.count("worker -c 6 ") != 1
        ):
            raise ValueError("retired worker command differs from reviewed source")
        source = source[:start] + source[end:]
    start, end = _program_span(source, PUB)
    section = source[start:end]
    if section.count(f"-Q {PUB}_queue ") != 1:
        raise ValueError("Pub worker command differs from reviewed source")
    section = _once(section, "worker -c 15 ", "worker -c 27 ")
    return source[:start] + section + source[end:]


def _local(source: str) -> str:
    for model in RETIRED:
        matching = [
            line
            for line in source.splitlines(keepends=True)
            if f"-Q {model}_queue " in line
        ]
        if (
            len(matching) != 1
            or "worker " not in matching[0]
            or " -c 2 " not in matching[0]
            or not matching[0].strip().endswith("&")
        ):
            raise ValueError(
                "retired LOCAL launch command differs from reviewed source"
            )
        source = _once(source, matching[0], "")
    matching = [
        line for line in source.splitlines(keepends=True) if f"-Q {PUB}_queue " in line
    ]
    if (
        len(matching) != 1
        or " -c 2 " not in matching[0]
        or LOCAL_CAPACITY_COMMENT in source
    ):
        raise ValueError("LOCAL Pub launch command differs from reviewed source")
    return _once(source, matching[0], LOCAL_CAPACITY_COMMENT + "\n" + matching[0])


def _queue_manual(source: str) -> str:
    for old, new in (
        (
            r"ms=[\"DeepSeek_V32\",\"DeepSeek_V4_Flash_My\",\"Pub_DeepSeek_V4_Flash\"]",
            r"ms=[\"Pub_DeepSeek_V4_Flash\"]",
        ),
        ("三模型负载快照", "Pub 模型负载快照"),
        (
            "排查任务为什么没进某个队列 / 队列是否健康",
            "排查 Pub 队列与模型容量是否健康",
        ),
        (
            "predicted 最低的模型会被优先选中",
            "predicted 用于观察积压，不再用于多模型分流",
        ),
        ("Pub_DeepSeek_V4_Flash / DeepSeek_V32 / DeepSeek_V4_Flash_My", PUB),
        ("1主 + 15子 = 16", "1主 + 27子 = 28"),
    ):
        source = _once(source, old, new)
    return source


def _cleanup(source: str) -> str:
    source = _once(
        source,
        "# prd: 分流到 DeepSeek_V4_Flash_My / DeepSeek_V32 / Deepseek_Qwen_32B_My 三个队列",
        "# local/prd: 统一 Pub_DeepSeek_V4_Flash_queue；旧队列在发版前排空",
    )
    old_queues = '["DeepSeek_V4_Flash_My_queue", "DeepSeek_V32_queue", "Pub_DeepSeek_V4_Flash_queue"]'
    for environment in ("local", "prd"):
        source = _once(
            source,
            f'    "{environment}": {old_queues},',
            f'    "{environment}": ["{PUB}_queue"],',
        )
    return source


def transforms(path: str, source: str) -> str:
    """Transform one reviewed source; the outer installer handles idempotence.

    Unknown or drifted input raises ValueError before any filesystem mutation.
    The source itself never appears in errors, since configs can hold secrets.
    """
    if path not in SUPPORTED_PATHS:
        raise ValueError("unsupported launcher/utility path")
    if path in {"config/celery_task.conf", "config/celery_task.detector.conf"}:
        changed = _supervisor(source)
    elif path in {"local_run.sh", "local_run.detector.sh"}:
        changed = _local(source)
    elif path == "my_workflows/util/check_celery_queue.sh":
        changed = _queue_manual(source)
    else:
        changed = _cleanup(source)
    if path != "validation/cleanup_priority9_tasks.py" and any(
        model in changed for model in RETIRED
    ):
        raise ValueError("unexpected retired model reference remains")
    if path.endswith(".py"):
        _parse(changed)
    return changed
