from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


progress_checker = _load_script("check_soc_progress")
upstream_checker = _load_script("check_upstream_drift")


ROADMAP = """# Roadmap

| Stage | Status | Outcome | Gate |
|---|---|---|---|
| `PI` Stage 4 | **Current / Active** | x | x |

| ID | Work |
|---|---|
| `PI-01` | Provider integration |
| `PI-06` | Governance |
| `UP-SYNC` | Upstream sync |
"""

PROGRESS = """# Progress

## Current Pointer / 当前指针

- **Current Stage:** `PI`
- **In Progress Task:** `PI-01`

## Recent Completion Records / 近期完成记录

### 2026-08-26 — Governance

- **Task:** `PI-06`
- **Status:** `Done`

## Update Contract / 更新约定

[archive](../archive/ai_soc/progress/README.md)
"""

ARCHIVE_INDEX = """# Archive

[2026-08](2026-08.md)
"""

ARCHIVE = """# Archive 2026-08

## 2026-08-25 — Historical record

- preserved
"""


def _write_fixture(root: Path, *, progress: str = PROGRESS, roadmap: str = ROADMAP, archive: str = ARCHIVE) -> None:
    notes = root / ".notes/ai_soc"
    archive_dir = root / ".notes/archive/ai_soc/progress"
    notes.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    (notes / "progress.md").write_text(progress, encoding="utf-8")
    (notes / "delivery-roadmap.md").write_text(roadmap, encoding="utf-8")
    (archive_dir / "README.md").write_text(ARCHIVE_INDEX, encoding="utf-8")
    (archive_dir / "2026-08.md").write_text(archive, encoding="utf-8")


def _codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def test_repository_progress_governance_passes() -> None:
    assert progress_checker.analyze(REPO_ROOT) == []


def test_valid_minimal_ledger_passes(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    assert progress_checker.analyze(tmp_path) == []


def test_duplicate_current_pointer_fails(tmp_path: Path) -> None:
    _write_fixture(tmp_path, progress=PROGRESS.replace("- **Current Stage:** `PI`", "- **Current Stage:** `PI`\n- **Current Stage:** `PI`"))

    assert "SOC003" in _codes(progress_checker.analyze(tmp_path))


def test_unknown_task_id_fails(tmp_path: Path) -> None:
    _write_fixture(tmp_path, progress=PROGRESS.replace("PI-01", "PI-99"))

    assert "SOC004" in _codes(progress_checker.analyze(tmp_path))


def test_recent_record_requires_task_and_terminal_status(tmp_path: Path) -> None:
    _write_fixture(tmp_path, progress=PROGRESS.replace("- **Task:** `PI-06`\n- **Status:** `Done`\n", ""))

    assert "SOC006" in _codes(progress_checker.analyze(tmp_path))


def test_experiment_manifest_requires_reproducibility_fields(tmp_path: Path) -> None:
    manifest = """
#### Experiment — incomplete

```json soc-experiment
{"experiment_id": "EXP-1", "task_id": "PI-06"}
```
"""
    progress = PROGRESS.replace("\n## Update Contract", manifest + "\n## Update Contract")
    _write_fixture(tmp_path, progress=progress)

    assert "SOC009" in _codes(progress_checker.analyze(tmp_path))


def test_complete_experiment_manifest_passes(tmp_path: Path) -> None:
    manifest = """
#### Experiment — complete

```json soc-experiment
{
  "experiment_id": "EXP-20260826-test",
  "task_id": "PI-06",
  "upstream_commit": "0123456789abcdef0123456789abcdef01234567",
  "model": "none",
  "config_hash": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "data_hash": "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
  "hardware": "test runner",
  "command": "python test.py",
  "metrics": {"passed": 1}
}
```
"""
    progress = PROGRESS.replace("\n## Update Contract", manifest + "\n## Update Contract")
    _write_fixture(tmp_path, progress=progress)

    assert progress_checker.analyze(tmp_path) == []


def test_monthly_archive_rejects_wrong_month(tmp_path: Path) -> None:
    _write_fixture(tmp_path, archive=ARCHIVE.replace("2026-08-25", "2026-07-25"))

    assert "SOC016" in _codes(progress_checker.analyze(tmp_path))


def test_monthly_archive_rejects_unknown_structured_task(tmp_path: Path) -> None:
    archive = ARCHIVE + "\n- **Task:** `PI-99`\n- **Status:** `Done`\n"
    _write_fixture(tmp_path, archive=archive)

    assert "SOC004" in _codes(progress_checker.analyze(tmp_path))


def test_monthly_archive_experiment_requires_manifest(tmp_path: Path) -> None:
    _write_fixture(tmp_path, archive=ARCHIVE + "\n#### Experiment — missing manifest\n")

    assert "SOC007" in _codes(progress_checker.analyze(tmp_path))


def test_active_progress_line_budget_is_enforced(tmp_path: Path) -> None:
    oversized = PROGRESS + "\n".join(f"line {index}" for index in range(250))
    _write_fixture(tmp_path, progress=oversized)

    assert "SOC002" in _codes(progress_checker.analyze(tmp_path))


def test_upstream_count_parser_and_threshold() -> None:
    assert upstream_checker.parse_counts("261\t0") == (261, 0)
    passing = upstream_checker.DriftReport(
        head="a" * 40,
        upstream_ref="upstream/main",
        upstream_commit="b" * 40,
        merge_base="b" * 40,
        ahead=261,
        behind=10,
        max_behind=10,
    )
    failing = upstream_checker.DriftReport(
        head="a" * 40,
        upstream_ref="upstream/main",
        upstream_commit="b" * 40,
        merge_base="c" * 40,
        ahead=261,
        behind=11,
        max_behind=10,
    )

    assert passing.passed is True
    assert failing.passed is False
