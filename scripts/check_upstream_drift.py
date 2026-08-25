#!/usr/bin/env python3
"""Measure fork ahead/behind state against a fetched upstream Git ref."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class DriftReport:
    head: str
    upstream_ref: str
    upstream_commit: str
    merge_base: str
    ahead: int
    behind: int
    max_behind: int

    @property
    def passed(self) -> bool:
        return self.behind <= self.max_behind


def parse_counts(output: str) -> tuple[int, int]:
    parts = output.split()
    if len(parts) != 2:
        raise ValueError(f"expected two rev-list counts, got: {output!r}")
    return int(parts[0]), int(parts[1])


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def measure_drift(root: Path, *, upstream_ref: str, max_behind: int) -> DriftReport:
    head = _git(root, "rev-parse", "HEAD")
    upstream_commit = _git(root, "rev-parse", upstream_ref)
    merge_base = _git(root, "merge-base", "HEAD", upstream_ref)
    ahead, behind = parse_counts(
        _git(root, "rev-list", "--left-right", "--count", f"HEAD...{upstream_ref}")
    )
    return DriftReport(
        head=head,
        upstream_ref=upstream_ref,
        upstream_commit=upstream_commit,
        merge_base=merge_base,
        ahead=ahead,
        behind=behind,
        max_behind=max_behind,
    )


def _write_github_summary(report: DriftReport) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    status = "PASS" if report.passed else "FAIL"
    text = f"## Upstream drift\n\n| Status | Ahead | Behind | Limit | Upstream commit |\n|---|---:|---:|---:|---|\n| {status} | {report.ahead} | {report.behind} | {report.max_behind} | `{report.upstream_commit}` |\n"
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--upstream-ref", default="upstream/main")
    parser.add_argument("--max-behind", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--github-summary", action="store_true")
    args = parser.parse_args(argv)
    if args.max_behind < 0:
        parser.error("--max-behind must be non-negative")
    try:
        report = measure_drift(
            args.root.resolve(),
            upstream_ref=args.upstream_ref,
            max_behind=args.max_behind,
        )
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"unable to measure upstream drift: {exc}", file=sys.stderr)
        return 2
    if args.github_summary:
        _write_github_summary(report)
    if args.json:
        payload = asdict(report)
        payload["passed"] = report.passed
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"upstream={report.upstream_commit} ahead={report.ahead} behind={report.behind} max_behind={report.max_behind}"
        )
    if not report.passed:
        print(
            f"upstream drift gate failed: behind {report.behind} exceeds limit {report.max_behind}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
