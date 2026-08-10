from __future__ import annotations

import json
import tarfile
from pathlib import Path

from scripts.build_pingan_macos_offline_bundle import (
    BUNDLE_ROOT,
    BUNDLE_SCHEMA,
    _bundle_readme,
    _file_record,
    _installer_script,
    _write_bundle_archive,
    inspect_bundle,
)


def test_offline_bundle_round_trip_verifies_artifact_digests(tmp_path: Path) -> None:
    artifacts = []
    for name, content in (
        ("uv-aarch64-apple-darwin.tar.gz", b"uv"),
        ("cpython.tar.gz", b"python"),
        ("uv-cache-macos-arm64-cp312.tar.gz", b"cache"),
    ):
        path = tmp_path / name
        path.write_bytes(content)
        artifacts.append(path)
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "target": {"os": "macos", "architecture": "arm64"},
        "artifacts": [_file_record(path) for path in artifacts],
        "offline_sync_verified": True,
    }
    bundle = tmp_path / "bundle.tar.gz"

    _write_bundle_archive(
        bundle,
        artifacts=artifacts,
        manifest=manifest,
        installer=_installer_script(),
        readme=_bundle_readme(),
    )
    result = inspect_bundle(bundle)

    assert result["manifest_valid"] is True
    assert result["safe_member_paths"] is True
    assert result["artifact_count"] == 3


def test_offline_installer_is_path_independent_and_network_disabled() -> None:
    installer = _installer_script()

    assert "/Users/zhangjianming627" not in installer
    assert "--offline" in installer
    assert "--no-python-downloads" in installer
    assert "--link-mode copy" in installer
    assert "aarch64-apple-darwin" in installer
    assert "backend/uv.lock does not match" in installer
    assert '(cd "$REPO_ROOT/backend"' in installer


def test_offline_bundle_contains_no_links(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"content")
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "target": {"os": "macos", "architecture": "arm64"},
        "artifacts": [_file_record(artifact)],
        "offline_sync_verified": True,
    }
    bundle = tmp_path / "bundle.tar.gz"
    _write_bundle_archive(
        bundle,
        artifacts=[artifact],
        manifest=manifest,
        installer=_installer_script(),
        readme=_bundle_readme(),
    )

    with tarfile.open(bundle, "r:gz") as archive:
        assert all(
            not member.issym() and not member.islnk() for member in archive.getmembers()
        )
        raw = archive.extractfile(f"{BUNDLE_ROOT}/MANIFEST.json")
        assert raw is not None
        assert json.loads(raw.read())["schema_version"] == BUNDLE_SCHEMA
