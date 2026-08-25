#!/usr/bin/env python3
"""Validate the active SOC execution ledger and its monthly archive."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_PROGRESS_LINES = 240
MAX_RECENT_RECORDS = 10

TASK_ID_PATTERN = (
    r"(?:BD|AUD|BG|PI|PA|UP|RID|GF|AA|EX|DP|EV|AC|D\d+)-[A-Z0-9]+(?:-[A-Z0-9]+)*"
)
TASK_ID_RE = re.compile(rf"`({TASK_ID_PATTERN})`")
CURRENT_STAGE_RE = re.compile(
    r"^- \*\*Current Stage:\*\* `([A-Z][A-Z0-9-]*)`\s*$", re.MULTILINE
)
IN_PROGRESS_TASK_RE = re.compile(
    rf"^- \*\*In Progress Task:\*\* `({TASK_ID_PATTERN})`\s*$", re.MULTILINE
)
ROADMAP_CURRENT_STAGE_RE = re.compile(
    r"^\| `([A-Z]{2})` .*?\| \*\*Current(?:\s|\s*/)", re.MULTILINE
)
RECENT_HEADING_RE = re.compile(r"^### (20\d{2}-\d{2}-\d{2}) — .+$", re.MULTILINE)
RECORD_TASK_RE = re.compile(
    rf"^- \*\*Task:\*\* `({TASK_ID_PATTERN})`\s*$", re.MULTILINE
)
RECORD_STATUS_RE = re.compile(
    r"^- \*\*Status:\*\* `(Done|Blocked|Superseded)`\s*$", re.MULTILINE
)
ARCHIVE_DATE_RE = re.compile(r"^#{2,4} (20\d{2}-\d{2}-\d{2})(?: —.*)?$", re.MULTILINE)
EXPERIMENT_HEADING_RE = re.compile(r"^#### Experiment(?: — .+)?$", re.MULTILINE)
EXPERIMENT_BLOCK_RE = re.compile(r"```json soc-experiment\s*\n(.*?)\n```", re.DOTALL)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Finding:
    code: str
    path: Path
    line: int
    message: str


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _recent_section(progress: str) -> str | None:
    marker = "## Recent Completion Records / 近期完成记录"
    start = progress.find(marker)
    if start < 0:
        return None
    body_start = start + len(marker)
    next_section = re.search(r"^## ", progress[body_start:], re.MULTILINE)
    end = body_start + next_section.start() if next_section else len(progress)
    return progress[body_start:end]


def _validate_experiments(
    *,
    text: str,
    path: Path,
    roadmap_ids: set[str],
    require_heading_parity: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    blocks = list(EXPERIMENT_BLOCK_RE.finditer(text))
    if require_heading_parity:
        heading_count = len(EXPERIMENT_HEADING_RE.findall(text))
        if heading_count != len(blocks):
            findings.append(
                Finding(
                    "SOC007",
                    path,
                    1,
                    f"found {heading_count} Experiment headings but {len(blocks)} soc-experiment JSON blocks",
                )
            )

    required = {
        "experiment_id",
        "task_id",
        "upstream_commit",
        "model",
        "config_hash",
        "data_hash",
        "hardware",
        "command",
        "metrics",
    }
    seen_ids: set[str] = set()
    for block in blocks:
        line = _line_number(text, block.start())
        try:
            payload = json.loads(block.group(1))
        except json.JSONDecodeError as exc:
            findings.append(
                Finding("SOC008", path, line, f"invalid experiment JSON: {exc.msg}")
            )
            continue
        if not isinstance(payload, dict):
            findings.append(
                Finding(
                    "SOC008", path, line, "experiment manifest must be a JSON object"
                )
            )
            continue
        missing = sorted(required - payload.keys())
        if missing:
            findings.append(
                Finding(
                    "SOC009",
                    path,
                    line,
                    f"experiment manifest missing: {', '.join(missing)}",
                )
            )
            continue

        experiment_id = payload["experiment_id"]
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            findings.append(
                Finding(
                    "SOC010", path, line, "experiment_id must be a non-empty string"
                )
            )
        elif experiment_id in seen_ids:
            findings.append(
                Finding(
                    "SOC010", path, line, f"duplicate experiment_id: {experiment_id}"
                )
            )
        else:
            seen_ids.add(experiment_id)

        task_id = payload["task_id"]
        if task_id not in roadmap_ids:
            findings.append(
                Finding(
                    "SOC004",
                    path,
                    line,
                    f"experiment task_id is absent from roadmap: {task_id}",
                )
            )
        if not isinstance(payload["upstream_commit"], str) or not COMMIT_RE.fullmatch(
            payload["upstream_commit"]
        ):
            findings.append(
                Finding(
                    "SOC011",
                    path,
                    line,
                    "upstream_commit must be a full 40-character Git SHA",
                )
            )
        for key in ("config_hash", "data_hash"):
            value = payload[key]
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                findings.append(
                    Finding(
                        "SOC012",
                        path,
                        line,
                        f"{key} must use sha256:<64 lowercase hex>",
                    )
                )
        for key in ("model", "hardware", "command"):
            value = payload[key]
            if not isinstance(value, str) or not value.strip():
                findings.append(
                    Finding("SOC013", path, line, f"{key} must be a non-empty string")
                )
        if not isinstance(payload["metrics"], dict) or not payload["metrics"]:
            findings.append(
                Finding("SOC014", path, line, "metrics must be a non-empty JSON object")
            )
    return findings


def analyze(root: Path) -> list[Finding]:
    progress_path = root / ".notes/ai_soc/progress.md"
    roadmap_path = root / ".notes/ai_soc/delivery-roadmap.md"
    archive_dir = root / ".notes/archive/ai_soc/progress"
    archive_index = archive_dir / "README.md"
    findings: list[Finding] = []

    for path in (progress_path, roadmap_path, archive_index):
        if not path.is_file():
            findings.append(
                Finding("SOC001", path, 1, "required governance file is missing")
            )
    if findings:
        return findings

    progress = progress_path.read_text(encoding="utf-8")
    roadmap = roadmap_path.read_text(encoding="utf-8")
    archive_index_text = archive_index.read_text(encoding="utf-8")
    roadmap_ids = set(TASK_ID_RE.findall(roadmap))

    progress_lines = len(progress.splitlines())
    if progress_lines > MAX_PROGRESS_LINES:
        findings.append(
            Finding(
                "SOC002",
                progress_path,
                1,
                f"active progress ledger has {progress_lines} lines; maximum is {MAX_PROGRESS_LINES}",
            )
        )

    stages = CURRENT_STAGE_RE.findall(progress)
    tasks = IN_PROGRESS_TASK_RE.findall(progress)
    if len(stages) != 1:
        findings.append(
            Finding(
                "SOC003",
                progress_path,
                1,
                f"expected exactly one Current Stage, found {len(stages)}",
            )
        )
    if len(tasks) != 1:
        findings.append(
            Finding(
                "SOC003",
                progress_path,
                1,
                f"expected exactly one In Progress Task, found {len(tasks)}",
            )
        )

    roadmap_current_stages = ROADMAP_CURRENT_STAGE_RE.findall(roadmap)
    if len(roadmap_current_stages) != 1:
        findings.append(
            Finding(
                "SOC003",
                roadmap_path,
                1,
                f"expected exactly one Current stage row in roadmap, found {len(roadmap_current_stages)}",
            )
        )
    elif stages and stages[0] != roadmap_current_stages[0]:
        findings.append(
            Finding(
                "SOC003",
                progress_path,
                1,
                f"Current Stage {stages[0]} disagrees with roadmap Current stage {roadmap_current_stages[0]}",
            )
        )
    if tasks and tasks[0] not in roadmap_ids:
        findings.append(
            Finding(
                "SOC004",
                progress_path,
                1,
                f"In Progress Task is absent from roadmap: {tasks[0]}",
            )
        )

    for match in TASK_ID_RE.finditer(progress):
        task_id = match.group(1)
        if task_id not in roadmap_ids:
            findings.append(
                Finding(
                    "SOC004",
                    progress_path,
                    _line_number(progress, match.start()),
                    f"task ID is absent from roadmap: {task_id}",
                )
            )

    recent = _recent_section(progress)
    if recent is None:
        findings.append(
            Finding(
                "SOC005",
                progress_path,
                1,
                "Recent Completion Records section is missing",
            )
        )
    else:
        headings = list(RECENT_HEADING_RE.finditer(recent))
        if len(headings) > MAX_RECENT_RECORDS:
            findings.append(
                Finding(
                    "SOC005",
                    progress_path,
                    1,
                    f"recent record count is {len(headings)}; maximum is {MAX_RECENT_RECORDS}",
                )
            )
        dates = [match.group(1) for match in headings]
        if dates != sorted(dates, reverse=True):
            findings.append(
                Finding(
                    "SOC005",
                    progress_path,
                    1,
                    "recent records must be ordered newest first",
                )
            )
        for index, heading in enumerate(headings):
            end = (
                headings[index + 1].start()
                if index + 1 < len(headings)
                else len(recent)
            )
            record = recent[heading.end() : end]
            record_line = _line_number(
                progress, progress.find(recent) + heading.start()
            )
            record_tasks = RECORD_TASK_RE.findall(record)
            record_statuses = RECORD_STATUS_RE.findall(record)
            if len(record_tasks) != 1:
                findings.append(
                    Finding(
                        "SOC006",
                        progress_path,
                        record_line,
                        "recent record must contain exactly one Task line",
                    )
                )
            elif record_tasks[0] not in roadmap_ids:
                findings.append(
                    Finding(
                        "SOC004",
                        progress_path,
                        record_line,
                        f"recent record task is absent from roadmap: {record_tasks[0]}",
                    )
                )
            if len(record_statuses) != 1:
                findings.append(
                    Finding(
                        "SOC006",
                        progress_path,
                        record_line,
                        "recent record must contain exactly one terminal Status: Done, Blocked, or Superseded",
                    )
                )

    if "../archive/ai_soc/progress/README.md" not in progress:
        findings.append(
            Finding(
                "SOC015",
                progress_path,
                1,
                "active progress ledger must link to the archive index",
            )
        )

    monthly_files = sorted(archive_dir.glob("20??-??.md"))
    if not monthly_files:
        findings.append(
            Finding("SOC015", archive_dir, 1, "no monthly progress archives found")
        )
    for monthly_path in monthly_files:
        month = monthly_path.stem
        text = monthly_path.read_text(encoding="utf-8")
        if f"({monthly_path.name})" not in archive_index_text:
            findings.append(
                Finding(
                    "SOC015",
                    archive_index,
                    1,
                    f"archive index does not link {monthly_path.name}",
                )
            )
        for match in RECORD_TASK_RE.finditer(text):
            task_id = match.group(1)
            if task_id not in roadmap_ids:
                findings.append(
                    Finding(
                        "SOC004",
                        monthly_path,
                        _line_number(text, match.start()),
                        f"archived record task is absent from roadmap: {task_id}",
                    )
                )
        for match in ARCHIVE_DATE_RE.finditer(text):
            if not match.group(1).startswith(month):
                findings.append(
                    Finding(
                        "SOC016",
                        monthly_path,
                        _line_number(text, match.start()),
                        f"record date {match.group(1)} does not belong in {monthly_path.name}",
                    )
                )
        findings.extend(
            _validate_experiments(
                text=text,
                path=monthly_path,
                roadmap_ids=roadmap_ids,
                require_heading_parity=True,
            )
        )

    findings.extend(
        _validate_experiments(
            text=progress,
            path=progress_path,
            roadmap_ids=roadmap_ids,
            require_heading_parity=True,
        )
    )
    return findings


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--github-annotations", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    findings = analyze(root)
    for finding in findings:
        relative = _display_path(finding.path, root)
        if args.github_annotations:
            print(
                f"::error file={relative},line={finding.line},title={finding.code}::{finding.message}"
            )
        else:
            print(
                f"{relative}:{finding.line}: {finding.code} {finding.message}",
                file=sys.stderr,
            )
    if findings:
        print(
            f"SOC progress governance failed with {len(findings)} finding(s).",
            file=sys.stderr,
        )
        return 1
    print("SOC progress governance passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
