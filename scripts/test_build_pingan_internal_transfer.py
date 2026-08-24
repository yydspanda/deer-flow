from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from scripts.build_pingan_internal_transfer import (
    ARCHIVE_ROOT,
    PRIVATE_ENV_REQUIRED_KEYS,
    PRIVATE_OVERLAY_PATHS,
    REQUIRED_HANDOFF_SOURCE_PATHS,
    TRANSFER_RUNBOOK_NAME,
    _archive_manifest,
    _assert_private_overlay_config_ready,
    _assert_required_handoff_sources,
    _assert_source_freeze_allowed,
    _assert_source_path_safe,
    _transfer_runbook,
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


def test_required_handoff_inventory_exists_in_current_repo() -> None:
    root = Path(__file__).resolve().parents[1]
    missing = [
        item for item in REQUIRED_HANDOFF_SOURCE_PATHS if not (root / item).is_file()
    ]

    assert missing == []


def test_private_overlay_contains_current_workbench_corpus_and_sidecars() -> None:
    paths = set(PRIVATE_OVERLAY_PATHS)

    assert {
        "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.manifest.json",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite",
    } <= paths


def test_transfer_runbook_uses_exact_archive_identity_without_hotfix() -> None:
    runbook = _transfer_runbook(
        timestamp="20260824T000000Z",
        git_info={"commit": "abc123", "branch": "yyds-dev"},
        archives={
            "source": {
                "path": "/tmp/deer-flow-pingan-source-20260824T000000Z.tar.gz",
                "sha256": "source-sha",
            },
            "private_overlay": {
                "path": "/tmp/deer-flow-pingan-private-overlay-20260824T000000Z.tar.gz",
                "sha256": "private-sha",
            },
        },
        report_name="transfer-report-20260824T000000Z.json",
    )

    assert "abc123" in runbook
    assert "source-sha" in runbook
    assert "private-sha" in runbook
    assert TRANSFER_RUNBOOK_NAME in runbook
    assert "full_alert_dams_labeled_merged.workbench-payloads.sqlite" in runbook
    assert "不需要额外 nginx/LAN hotfix" in runbook


def test_private_overlay_config_accepts_current_dynamic_profile(tmp_path: Path) -> None:
    _write_private_profiles(tmp_path)

    _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_rejects_obsolete_import_contract(
    tmp_path: Path,
) -> None:
    _write_private_profiles(
        tmp_path,
        extra='export SOC_PINGAN_WORKFLOW_RUNNER_IMPORT="legacy:run_workflow"\n',
    )

    with pytest.raises(ValueError, match="obsolete keys"):
        _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_rejects_placeholder_or_user_path(
    tmp_path: Path,
) -> None:
    _write_private_profiles(
        tmp_path,
        overrides={"SOC_PINGAN_WORKFLOW_APP_SECRET": "<internal-secret>"},
    )
    with pytest.raises(ValueError, match="unresolved values"):
        _assert_private_overlay_config_ready(tmp_path)

    _write_private_profiles(tmp_path, config_text="path: /Users/example/deer-flow\n")
    with pytest.raises(ValueError, match="developer-specific path"):
        _assert_private_overlay_config_ready(tmp_path)


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


def _write_private_profiles(
    root: Path,
    *,
    overrides: dict[str, str] | None = None,
    extra: str = "",
    config_text: str = "models: []\n",
) -> None:
    values = {name: f"value-{name.lower()}" for name in PRIVATE_ENV_REQUIRED_KEYS}
    values.update(overrides or {})
    env_path = root / ".env.soc-dev.local"
    env_path.write_text(
        "".join(f'export {name}="{value}"\n' for name, value in sorted(values.items()))
        + extra,
        encoding="utf-8",
    )
    config_path = root / "config.pingan-dev.local"
    config_path.write_text(config_text, encoding="utf-8")
    env_path.chmod(0o600)
    config_path.chmod(0o600)
