"""Seal SOC Alpha acceptance and regression evidence into a readiness report."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / ".deer-flow" / "soc-alpha-readiness"
COMPLETENESS_MATRIX = REPOSITORY_ROOT / ".notes" / "ai_soc" / "audits" / "alpha-completeness-matrix.md"
DELIVERY_ROADMAP = REPOSITORY_ROOT / ".notes" / "ai_soc" / "delivery-roadmap.md"
ACCEPTANCE_REPORT_RELATIVE_PATH = Path("soc-alpha-acceptance") / "alpha-acceptance-report.json"

REPORT_SCHEMA_VERSION = "soc.alpha_readiness_report.v1"
TEST_GATE_SCHEMA_VERSION = "soc.alpha_test_gate.v1"
ACCEPTANCE_SCHEMA_VERSION = "soc.alpha_acceptance_report.v1"
EXPECTED_TEST_GATES = ("backend-soc", "architecture-migrations")
EXPECTED_COMPLETENESS_STATES = ("Complete", "Gap", "Mock", "Data-gated", "Deferred", "Total")


class ReadinessFailure(RuntimeError):
    """Raised when a readiness artifact cannot be safely produced."""


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir).resolve()

    if args.command == "prepare":
        prepare_output_dir(output_dir)
        return 0
    if args.command == "record-gate":
        gate = record_pytest_gate(
            output_dir=output_dir,
            gate_name=args.gate,
            exit_code=args.exit_code,
            command=args.test_command,
            log_file=Path(args.log_file),
        )
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        return 0 if gate["status"] == "passed" else 1
    if args.command == "finalize":
        report = finalize_readiness_report(output_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    raise AssertionError(f"unhandled command: {args.command}")


def prepare_output_dir(output_dir: Path) -> None:
    """Clear only an explicitly named readiness evidence directory."""

    if "soc-alpha-readiness" not in output_dir.name.lower():
        raise ReadinessFailure("refusing to clear a readiness output directory whose basename does not contain 'soc-alpha-readiness'")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def record_pytest_gate(
    *,
    output_dir: Path,
    gate_name: str,
    exit_code: int,
    command: str,
    log_file: Path,
) -> dict[str, Any]:
    """Record one pytest command without trusting shell exit status alone."""

    if gate_name not in EXPECTED_TEST_GATES:
        raise ReadinessFailure(f"unsupported test gate: {gate_name}")
    output_dir = output_dir.resolve()
    resolved_log = log_file.resolve()
    if not resolved_log.is_relative_to(output_dir):
        raise ReadinessFailure("test gate log must live inside the readiness output directory")

    log_text = resolved_log.read_text(encoding="utf-8", errors="replace") if resolved_log.exists() else ""
    summary = parse_pytest_summary(log_text)
    failure_reasons: list[str] = []
    if exit_code != 0:
        failure_reasons.append(f"pytest exited with status {exit_code}")
    if summary["passed"] <= 0:
        failure_reasons.append("pytest output did not prove any passing tests")
    if summary["failed"] or summary["errors"]:
        failure_reasons.append("pytest output contains failed or error results")

    result = {
        "schema_version": TEST_GATE_SCHEMA_VERSION,
        "gate": gate_name,
        "status": "passed" if not failure_reasons else "failed",
        "recorded_at": _utc_now(),
        "exit_code": exit_code,
        "command": command,
        "log_path": resolved_log.relative_to(output_dir).as_posix(),
        "summary": summary,
        "failure_reasons": failure_reasons,
    }
    _write_json(output_dir / "tests" / f"{gate_name}.status.json", result)
    return result


def parse_pytest_summary(log_text: str) -> dict[str, int | float | None]:
    """Extract stable pytest totals from the final terminal summary."""

    def count(label: str) -> int:
        matches = re.findall(rf"(\d+)\s+{label}\b", log_text)
        return int(matches[-1]) if matches else 0

    duration_matches = re.findall(r"\bin\s+([0-9]+(?:\.[0-9]+)?)s\b", log_text)
    return {
        "passed": count("passed"),
        "failed": count("failed"),
        "errors": count("errors?"),
        "skipped": count("skipped"),
        "duration_seconds": float(duration_matches[-1]) if duration_matches else None,
    }


def finalize_readiness_report(
    output_dir: Path,
    *,
    completeness_matrix: Path = COMPLETENESS_MATRIX,
    delivery_roadmap: Path = DELIVERY_ROADMAP,
) -> dict[str, Any]:
    """Validate evidence and seal the BG-03 owner-review candidate."""

    output_dir = output_dir.resolve()
    acceptance_path = output_dir / ACCEPTANCE_REPORT_RELATIVE_PATH
    failure_reasons: list[str] = []

    acceptance = _read_json_or_failure(
        acceptance_path,
        schema_version=ACCEPTANCE_SCHEMA_VERSION,
        reason="Alpha acceptance report is missing or invalid",
    )
    acceptance_passed = acceptance.get("schema_version") == ACCEPTANCE_SCHEMA_VERSION and acceptance.get("status") == "passed"
    if not acceptance_passed:
        failure_reasons.append("versioned Alpha acceptance report did not pass")

    test_gates: dict[str, dict[str, Any]] = {}
    for gate_name in EXPECTED_TEST_GATES:
        gate = _read_json_or_failure(
            output_dir / "tests" / f"{gate_name}.status.json",
            schema_version=TEST_GATE_SCHEMA_VERSION,
            reason=f"test gate is missing or invalid: {gate_name}",
        )
        gate_passed = gate.get("schema_version") == TEST_GATE_SCHEMA_VERSION and gate.get("gate") == gate_name and gate.get("status") == "passed"
        if not gate_passed:
            failure_reasons.append(f"required test gate did not pass: {gate_name}")
        test_gates[gate_name] = gate

    try:
        matrix_text = completeness_matrix.read_text(encoding="utf-8")
        completeness_counts = parse_completeness_counts(matrix_text)
        data_gated_ids = parse_capability_ids(matrix_text, "## 4. Data-Gated Register")
        deferred_ids = parse_capability_ids(matrix_text, "## 5. Deferred Register")
    except (OSError, ReadinessFailure) as exc:
        completeness_counts = {}
        data_gated_ids = []
        deferred_ids = []
        failure_reasons.append(f"completeness matrix could not be verified: {type(exc).__name__}: {exc}")
    if completeness_counts.get("Gap") != 0:
        failure_reasons.append("code-controllable Alpha gap count is not zero")

    try:
        roadmap_text = delivery_roadmap.read_text(encoding="utf-8")
        stage4_packages = parse_work_package_ids(roadmap_text, "## 6. Stage 4 - Real Data & Production Integration")
    except (OSError, ReadinessFailure) as exc:
        stage4_packages = []
        failure_reasons.append(f"Stage 4 handoff could not be verified: {type(exc).__name__}: {exc}")

    technical_status = "passed" if not failure_reasons else "failed"
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "stage_task_id": "BG-03",
        "generated_at": _utc_now(),
        "status": technical_status,
        "alpha_candidate_ready": technical_status == "passed",
        "release_decision": "pending_owner_review",
        "stage_transition_allowed": False,
        "production_ready": False,
        "source": _git_source_state(REPOSITORY_ROOT),
        "acceptance": {
            "schema_version": acceptance.get("schema_version"),
            "status": acceptance.get("status"),
            "generated_at": acceptance.get("generated_at"),
            "report_path": ACCEPTANCE_REPORT_RELATIVE_PATH.as_posix(),
            "sha256": _sha256_file(acceptance_path) if acceptance_path.exists() else None,
            "component_status": acceptance.get("component_status", {}),
        },
        "test_gates": test_gates,
        "completeness": {
            "source_document": _display_path(completeness_matrix),
            "source_sha256": _sha256_file(completeness_matrix) if completeness_matrix.exists() else None,
            "counts": completeness_counts,
            "data_gated_capability_ids": data_gated_ids,
            "deferred_capability_ids": deferred_ids,
        },
        "claim_boundaries": acceptance.get("mock_and_data_boundaries", []),
        "stage4_handoff": {
            "source_document": _display_path(delivery_roadmap),
            "source_sha256": _sha256_file(delivery_roadmap) if delivery_roadmap.exists() else None,
            "work_packages": stage4_packages,
            "owner_review_required": True,
        },
        "deployment_and_rollback_runbook": ".notes/ai_soc/alpha-readiness-package.md",
        "owner_signoff": {
            "status": "pending",
            "required_roles": ["product_owner", "soc_operations", "security", "platform_infrastructure"],
        },
        "artifact_manifest": _readiness_artifact_manifest(output_dir),
        "failure_reasons": failure_reasons,
    }
    _write_json(output_dir / "alpha-readiness-report.json", report)
    return report


def parse_completeness_counts(markdown: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_line in markdown.splitlines():
        line = raw_line.replace("**", "")
        match = re.fullmatch(r"\|\s*(Complete|Gap|Mock|Data-gated|Deferred|Total)\s*\|\s*(\d+)\s*\|", line)
        if match:
            counts[match.group(1)] = int(match.group(2))
    missing = [state for state in EXPECTED_COMPLETENESS_STATES if state not in counts]
    if missing:
        raise ReadinessFailure(f"completeness matrix summary is missing states: {', '.join(missing)}")
    if sum(counts[state] for state in EXPECTED_COMPLETENESS_STATES[:-1]) != counts["Total"]:
        raise ReadinessFailure("completeness matrix state counts do not add up to Total")
    return counts


def parse_capability_ids(markdown: str, heading: str) -> list[str]:
    section = _markdown_section(markdown, heading)
    capability_ids = sorted(set(re.findall(r"(?m)^\|\s*`(AC-\d+)`[^|\n]*\|", section)))
    if not capability_ids:
        raise ReadinessFailure(f"no capability IDs found under {heading}")
    return capability_ids


def parse_work_package_ids(markdown: str, heading: str) -> list[str]:
    section = _markdown_section(markdown, heading)
    package_ids = sorted(set(re.findall(r"\|\s*`(PI-\d+)`\s*\|", section)))
    if not package_ids:
        raise ReadinessFailure(f"no work package IDs found under {heading}")
    return package_ids


def _markdown_section(markdown: str, heading: str) -> str:
    start = markdown.find(heading)
    if start < 0:
        raise ReadinessFailure(f"missing Markdown section: {heading}")
    next_heading = markdown.find("\n## ", start + len(heading))
    return markdown[start:] if next_heading < 0 else markdown[start:next_heading]


def _readiness_artifact_manifest(output_dir: Path) -> list[dict[str, Any]]:
    relative_paths = [
        ACCEPTANCE_REPORT_RELATIVE_PATH,
        Path("tests") / "backend-soc.log",
        Path("tests") / "backend-soc.status.json",
        Path("tests") / "architecture-migrations.log",
        Path("tests") / "architecture-migrations.status.json",
    ]
    artifacts: list[dict[str, Any]] = []
    for relative_path in relative_paths:
        path = output_dir / relative_path
        if not path.is_file():
            continue
        artifacts.append(
            {
                "path": relative_path.as_posix(),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return artifacts


def _git_source_state(repository_root: Path) -> dict[str, Any]:
    commit = _git_output(repository_root, "rev-parse", "HEAD")
    branch = _git_output(repository_root, "branch", "--show-current")
    tracked_changes = _git_output(repository_root, "diff", "--name-only").splitlines()
    staged_changes = _git_output(repository_root, "diff", "--cached", "--name-only").splitlines()
    untracked = _git_output(repository_root, "ls-files", "--others", "--exclude-standard").splitlines()
    return {
        "commit": commit or None,
        "branch": branch or None,
        "worktree_clean": not tracked_changes and not staged_changes and not untracked,
        "tracked_change_count": len(tracked_changes),
        "staged_change_count": len(staged_changes),
        "untracked_path_count": len(untracked),
        "clean_checkout_required_for_release_archive": True,
    }


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _git_output(repository_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _read_json_or_failure(path: Path, *, schema_version: str, reason: str) -> dict[str, Any]:
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
    return {"schema_version": schema_version, "status": "failed", "failure_reasons": [reason]}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SOC Alpha readiness evidence")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Gitignored readiness output directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare", help="Clear the known readiness output tree")

    record = subparsers.add_parser("record-gate", help="Record one pytest gate and parse its summary")
    record.add_argument("--gate", required=True, choices=EXPECTED_TEST_GATES)
    record.add_argument("--exit-code", required=True, type=int)
    record.add_argument("--test-command", required=True)
    record.add_argument("--log-file", required=True)

    subparsers.add_parser("finalize", help="Seal acceptance, regression, and handoff evidence")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
