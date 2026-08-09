from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from scripts.build_pingan_internal_transfer import (
    ARCHIVE_ROOT,
    REQUIRED_HANDOFF_SOURCE_PATHS,
    _archive_manifest,
    _assert_required_handoff_sources,
    _assert_source_freeze_allowed,
    _assert_source_path_safe,
    _write_archive,
    inspect_archive,
)


def test_transfer_freeze_rejects_dirty_worktree_by_default() -> None:
    with pytest.raises(ValueError, match="worktree is dirty"):
        _assert_source_freeze_allowed(
            {"worktree_dirty": True},
            allow_dirty=False,
        )


def test_transfer_freeze_allows_explicit_development_override() -> None:
    _assert_source_freeze_allowed(
        {"worktree_dirty": True},
        allow_dirty=True,
    )


def test_transfer_freeze_allows_clean_worktree() -> None:
    _assert_source_freeze_allowed(
        {"worktree_dirty": False},
        allow_dirty=False,
    )


def test_transfer_freeze_requires_complete_handoff_inventory() -> None:
    with pytest.raises(
        ValueError, match="required internal handoff source files are missing"
    ):
        _assert_required_handoff_sources([Path(REQUIRED_HANDOFF_SOURCE_PATHS[0])])


def test_transfer_freeze_accepts_complete_handoff_inventory() -> None:
    _assert_required_handoff_sources(
        [Path(item) for item in REQUIRED_HANDOFF_SOURCE_PATHS]
    )


def test_transfer_archive_round_trip_verifies_manifest_digests(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    relative = Path("backend/soc_agent/example.py")
    source = root / relative
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    manifest = _archive_manifest(
        archive_kind="source",
        root=root,
        paths=[relative],
        created_at="20260804T000000Z",
        git_info={"commit": "abc", "branch": "dev", "worktree_dirty": True},
        private=False,
    )
    archive = tmp_path / "source.tar.gz"

    _write_archive(
        archive,
        root=root,
        paths=[relative],
        manifest=manifest,
        readme="internal transfer\n",
    )
    report = inspect_archive(archive)

    assert report["kind"] == "source"
    assert report["file_count"] == 1
    assert report["verified_file_count"] == 1
    assert archive.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "relative",
    [
        Path(".env.soc-dev.local"),
        Path("config.pingan-dev.local"),
        Path("datas/private.pkl"),
        Path("data/catalog.sqlite"),
        Path("knowledge.xlsx"),
    ],
)
def test_source_archive_rejects_private_inputs(relative: Path) -> None:
    with pytest.raises(ValueError):
        _assert_source_path_safe(relative)


def test_inspect_rejects_escaping_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        manifest = tarfile.TarInfo(f"{ARCHIVE_ROOT}/TRANSFER-MANIFEST.source.json")
        payload = b'{"archive_kind":"source","files":[]}'
        manifest.size = len(payload)
        handle.addfile(manifest, io.BytesIO(payload))
        link = tarfile.TarInfo(f"{ARCHIVE_ROOT}/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        handle.addfile(link)

    with pytest.raises(ValueError, match="unsafe archive symlink target"):
        inspect_archive(archive)
