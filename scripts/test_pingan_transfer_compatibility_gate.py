"""Formal transfer builds require fresh compatibility evidence before publication."""

import errno
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import build_pingan_internal_transfer as builder


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "README.md").write_text("frozen source\n", encoding="utf-8")
    monkeypatch.setattr(
        builder,
        "_git_info",
        lambda _: {"commit": "a" * 40, "branch": "test", "worktree_dirty": False},
    )
    monkeypatch.setattr(
        builder,
        "collect_source_paths",
        lambda _: sorted(p.relative_to(root) for p in root.glob("*.md")),
    )
    monkeypatch.setattr(builder, "_assert_required_handoff_sources", lambda _: None)
    return root


def _passed(root, *, source_commit, source_fingerprint):
    return {
        "status": "passed",
        "source_commit": source_commit,
        "source_fingerprint": source_fingerprint,
        "passed": 3,
    }


def _existing_release(output):
    output.mkdir()
    files = [
        builder.TRANSFER_INSTALLER_NAME,
        builder.TRANSFER_RUNBOOK_NAME,
        "previous.tar.gz",
        "previous-report.json",
    ]
    for name in files:
        (output / name).write_bytes(b"previous verified release")
    return {p.name: p.read_bytes() for p in output.iterdir()}


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("compatibility failed"),
        TimeoutError("compatibility timeout"),
        FileNotFoundError("pytest unavailable"),
    ],
)
def test_gate_failure_preserves_ready_directory(
    checkout, tmp_path, monkeypatch, failure
):
    output = tmp_path / "READY"
    before = _existing_release(output)

    def reject(*args, **kwargs):
        raise failure

    monkeypatch.setattr(builder, "run_compatibility_gate", reject, raising=False)
    with pytest.raises(type(failure), match=str(failure)):
        builder.build_transfer_archives(root=checkout, output_dir=output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


@pytest.mark.parametrize("change", ["content", "added", "removed", "commit"])
def test_changed_source_after_tests_cannot_be_published(
    checkout, tmp_path, monkeypatch, change
):
    output = tmp_path / "READY"
    before = _existing_release(output)

    def drifting(root, **kwargs):
        if change == "content":
            (root / "README.md").write_text("untested source", encoding="utf-8")
        elif change == "added":
            (root / "ADDED.md").write_text("untested source", encoding="utf-8")
        elif change == "removed":
            (root / "README.md").unlink()
        else:
            monkeypatch.setattr(
                builder,
                "_git_info",
                lambda _: {
                    "commit": "b" * 40,
                    "branch": "test",
                    "worktree_dirty": False,
                },
            )
        return _passed(root, **kwargs)

    monkeypatch.setattr(builder, "run_compatibility_gate", drifting, raising=False)
    with pytest.raises(ValueError, match="source.*changed"):
        builder.build_transfer_archives(root=checkout, output_dir=output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


@pytest.mark.parametrize("dirty", [False, True])
def test_receipt_is_bound_to_archive_and_development_builds_still_test(
    checkout, tmp_path, monkeypatch, dirty
):
    calls = []
    monkeypatch.setattr(
        builder,
        "_git_info",
        lambda _: {"commit": "a" * 40, "branch": "test", "worktree_dirty": dirty},
    )

    def passed(root, **kwargs):
        calls.append(kwargs)
        return _passed(root, **kwargs)

    monkeypatch.setattr(builder, "run_compatibility_gate", passed, raising=False)
    output = tmp_path / "READY"
    report = builder.build_transfer_archives(
        root=checkout, output_dir=output, allow_dirty=dirty
    )
    saved = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert saved["compatibility_check"]["source_commit"] == "a" * 40
    assert (
        saved["compatibility_check"]["source_fingerprint"]
        == calls[0]["source_fingerprint"]
    )
    assert len(calls[0]["source_fingerprint"]) == 64
    assert (
        saved["compatibility_check"]["source_archive_sha256"]
        == saved["archives"]["source"]["sha256"]
    )
    assert saved["final_handoff_eligible"] is (not dirty)
    for item in [
        *saved["archives"].values(),
        saved["installer"],
        *saved["runbooks"].values(),
    ]:
        path = Path(item["path"])
        assert path.parent == output
        assert builder._sha256_file(path) == item["sha256"]
    assert not any(p.name.startswith(".") for p in output.iterdir())


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "skipped"),
        ("source_commit", "old-commit"),
        ("source_fingerprint", "old-source"),
    ],
)
def test_nonmatching_receipt_is_not_accepted(
    checkout, tmp_path, monkeypatch, field, value
):
    def wrong(root, **kwargs):
        return {**_passed(root, **kwargs), field: value}

    monkeypatch.setattr(builder, "run_compatibility_gate", wrong, raising=False)
    with pytest.raises(ValueError, match="compatibility"):
        builder.build_transfer_archives(root=checkout, output_dir=tmp_path / "READY")


def test_archive_validation_failure_does_not_replace_verified_release(
    checkout, tmp_path, monkeypatch
):
    output = tmp_path / "READY"
    before = _existing_release(output)
    monkeypatch.setattr(builder, "run_compatibility_gate", _passed, raising=False)

    def reject(_):
        raise ValueError("archive digest mismatch")

    monkeypatch.setattr(builder, "inspect_archive", reject)
    with pytest.raises(ValueError, match="archive digest mismatch"):
        builder.build_transfer_archives(root=checkout, output_dir=output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_source_edit_during_archive_write_is_rejected(checkout, tmp_path, monkeypatch):
    output = tmp_path / "READY"
    before = _existing_release(output)
    monkeypatch.setattr(builder, "run_compatibility_gate", _passed, raising=False)
    original_write = builder._write_archive

    def drifting(*args, **kwargs):
        (checkout / "README.md").write_text(
            "changed during archive write", encoding="utf-8"
        )
        original_write(*args, **kwargs)

    monkeypatch.setattr(builder, "_write_archive", drifting)
    with pytest.raises(ValueError, match="archive digest mismatch"):
        builder.build_transfer_archives(root=checkout, output_dir=output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_same_name_release_is_never_overwritten(checkout, tmp_path, monkeypatch):
    class FixedTime:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 20, tzinfo=UTC)

    monkeypatch.setattr(builder, "datetime", FixedTime)
    monkeypatch.setattr(builder, "run_compatibility_gate", _passed, raising=False)
    output = tmp_path / "READY"
    builder.build_transfer_archives(root=checkout, output_dir=output)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError, match="already exists"):
        builder.build_transfer_archives(root=checkout, output_dir=output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_publication_supports_output_on_another_filesystem(
    checkout, tmp_path, monkeypatch
):
    output = tmp_path / "READY"
    replace = builder.os.replace

    def same_device_only(source, target):
        if Path(target).parent == output and output not in Path(source).parents:
            raise OSError(errno.EXDEV, "cross-device link")
        return replace(source, target)

    monkeypatch.setattr(builder, "run_compatibility_gate", _passed)
    monkeypatch.setattr(builder.os, "replace", same_device_only)
    report = builder.build_transfer_archives(root=checkout, output_dir=output)
    assert Path(report["report_path"]).is_file()
    assert not any(p.name.startswith(".") for p in output.iterdir())


def test_publication_failure_restores_previous_release(checkout, tmp_path, monkeypatch):
    output = tmp_path / "READY"
    before = _existing_release(output)
    replace = builder.os.replace
    failed = False

    def fail_once(source, target):
        nonlocal failed
        if Path(target) == output / builder.TRANSFER_RUNBOOK_NAME and not failed:
            failed = True
            raise OSError("simulated publication failure")
        return replace(source, target)

    monkeypatch.setattr(builder, "run_compatibility_gate", _passed)
    monkeypatch.setattr(builder.os, "replace", fail_once)
    with pytest.raises(OSError, match="simulated publication failure"):
        builder.build_transfer_archives(root=checkout, output_dir=output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_interrupt_during_backup_preserves_previous_release(
    checkout, tmp_path, monkeypatch
):
    output = tmp_path / "READY"
    before = _existing_release(output)
    replace = builder.os.replace
    copy = builder.shutil.copy2

    def interrupt_after_backup(operation, source, target, **kwargs):
        result = operation(source, target, **kwargs)
        if Path(target).parent.name == "backups":
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(builder, "run_compatibility_gate", _passed)
    monkeypatch.setattr(
        builder.os, "replace", lambda *args: interrupt_after_backup(replace, *args)
    )
    monkeypatch.setattr(
        builder.shutil,
        "copy2",
        lambda *args, **kwargs: interrupt_after_backup(copy, *args, **kwargs),
    )
    with pytest.raises(KeyboardInterrupt):
        builder.build_transfer_archives(root=checkout, output_dir=output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
