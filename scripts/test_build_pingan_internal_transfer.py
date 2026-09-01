from __future__ import annotations

import io
import re
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


def test_private_overlay_keeps_corpus_metadata_but_excludes_large_data() -> None:
    paths = set(PRIVATE_OVERLAY_PATHS)

    assert {
        ".secrets/eagw-private-key.der",
        "validation/compact_zeus/data/corpus/full_alert_validation_corpus.manifest.json",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.manifest.json",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json",
    } <= paths
    assert {
        "datas/source/full_alert_2026_month_forth_sample_200.pkl",
        "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite",
    }.isdisjoint(paths)


def test_handoff_uses_project_model_gateway_and_legacy_execution_plane() -> None:
    required = set(REQUIRED_HANDOFF_SOURCE_PATHS)

    assert "backend/scripts/soc_pingan_model_gateway.py" in required
    assert "backend/scripts/soc_pingan_legacy_api.py" in required
    assert "backend/scripts/soc_pingan_legacy_worker.py" in required
    assert "backend/scripts/soc_pingan_legacy_fake_acceptance.py" in required
    assert "backend/scripts/soc_pingan_prepare_legacy_live_request.py" in required
    assert "backend/scripts/soc_pingan_set_legacy_provider_mode.py" in required
    assert (
        "backend/scripts/soc_pingan_prepare_legacy_model_gateway_profile.py" in required
    )
    assert "scripts/soc_pingan_host_sidecars.py" in required
    assert "scripts/soc_pingan_stage_internal_corpus.py" in required
    assert "backend/scripts/soc_pingan_litellm_smoke.py" not in required


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
    assert (
        "$HOME/Downloads/source/full_alert_2026_month_forth_sample_200.pkl" in runbook
    )
    assert "$HOME/Downloads/corpus/full_alert_dams_labeled_merged.pkl" in runbook
    assert "soc_pingan_stage_internal_corpus.py --apply" in runbook
    assert "停止旧 Host DEV" in runbook
    assert "for port in 3000 8001 2026 4001 8090" in runbook
    assert runbook.index("soc_pingan_macos_host_dev.py stop") < runbook.index(
        'rm -rf "$TARGET_REPO"'
    )
    assert "三个 PKL 和 Workbench payload SQLite 不在 private overlay" in runbook
    assert "不需要额外 nginx/LAN hotfix" in runbook
    assert "不得再启动 `$HOME/sec_know_model`、LiteLLM、Celery 或 Redis" in runbook
    assert "soc_pingan_model_gateway_smoke.py" in runbook
    assert "`thinking_requested=false`" in runbook
    assert "`max_tokens_requested=128`" in runbook
    assert "soc_pingan_legacy_fake_acceptance.py" in runbook
    assert "无敏感合成协议夹具" in runbook
    assert "不读取历史小 JSON" in runbook
    assert "soc_pingan_legacy_live_acceptance.py" in runbook
    assert "soc_pingan_prepare_legacy_live_request.py" in runbook
    assert "soc_pingan_set_legacy_provider_mode.py" in runbook
    assert "手工编辑请求 JSON" in runbook
    assert "编辑该文件并替换全部 placeholder" not in runbook
    assert (
        "cp backend/samples/pingan_dev/legacy-task-request.example.json" not in runbook
    )
    assert "soc_pingan_prepare_legacy_model_gateway_profile.py" in runbook
    assert ".secrets/eagw-private-key.der" in runbook
    local_env_blocks = [
        block
        for block in re.findall(r"```bash\n(.*?)```", runbook, flags=re.DOTALL)
        if "source ./.env.soc-dev.local" in block
    ]
    assert len(local_env_blocks) == 3
    for block in local_env_blocks:
        assert 'export TARGET_REPO="${TARGET_REPO:-$HOME/deer-flow}"' in block
        assert 'cd "$TARGET_REPO"' in block
        assert (
            'eval "$(backend/.venv/bin/python '
            'backend/scripts/soc_pingan_local_paths.py --shell)"' in block
        )
    assert "proves_real_internal_connectivity=true" in runbook
    assert "task-request.local.json" in runbook
    assert 'shasum -a 256 \\\n  "deer-flow-pingan-source' in runbook
    assert (
        "chmod 600 .env.soc-dev.local config.pingan-dev.local \\\n"
        "  .secrets/eagw-private-key.der"
    ) in runbook
    assert "soc_pingan_model_gateway_smoke.py \\\n  --confirm-live" in runbook
    assert '--database-url "sqlite+pysqlite:///$SOC_DEV_SQLITE_PATH"' in runbook


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


def test_private_overlay_config_rejects_stale_config_version(tmp_path: Path) -> None:
    _write_private_profiles(
        tmp_path,
        config_text="config_version: 37\nmodels: []\n",
    )

    with pytest.raises(ValueError, match="config_version"):
        _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_rejects_obsolete_litellm_model_profile(
    tmp_path: Path,
) -> None:
    _write_private_profiles(
        tmp_path,
        config_text="""config_version: 38
models:
  - api_base: $PINGAN_LITELLM_BASE_URL
    api_key: $PINGAN_LITELLM_API_KEY
database:
  backend: sqlite
  sqlite_dir: .deer-flow/data
""",
    )

    with pytest.raises(ValueError, match="obsolete LiteLLM"):
        _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_requires_model_gateway_and_sqlite(
    tmp_path: Path,
) -> None:
    _write_private_profiles(
        tmp_path,
        config_text="config_version: 38\nmodels: []\n",
    )

    with pytest.raises(ValueError, match="model-gateway references"):
        _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_rejects_permissive_or_missing_gateway_key(
    tmp_path: Path,
) -> None:
    _write_private_profiles(tmp_path)
    key_path = tmp_path / ".secrets/eagw-private-key.der"
    key_path.chmod(0o644)
    with pytest.raises(ValueError, match="RSA key must be mode 0600"):
        _assert_private_overlay_config_ready(tmp_path)

    key_path.unlink()
    with pytest.raises(ValueError, match="missing the prepared EAGW RSA key"):
        _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_rejects_live_or_incoherent_transfer_profile(
    tmp_path: Path,
) -> None:
    _write_private_profiles(
        tmp_path,
        overrides={"SOC_PINGAN_LEGACY_CALLBACK_MODE": "internal"},
    )
    with pytest.raises(ValueError, match="safe transfer profile"):
        _assert_private_overlay_config_ready(tmp_path)

    _write_private_profiles(
        tmp_path,
        overrides={"SOC_PINGAN_MODEL_GATEWAY_API_KEYS": "different-key"},
    )
    with pytest.raises(ValueError, match="client key is not accepted"):
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
        Path(".secrets/eagw-private-key.der"),
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
    config_text: str = """config_version: 38
models:
  - model: deepseek-v4-flash
    api_base: $PINGAN_MODEL_GATEWAY_BASE_URL
    api_key: $PINGAN_MODEL_GATEWAY_API_KEY
database:
  backend: sqlite
  sqlite_dir: .deer-flow/data
""",
) -> None:
    (root / "config.example.yaml").write_text(
        "config_version: 38\n",
        encoding="utf-8",
    )
    values = {name: f"value-{name.lower()}" for name in PRIVATE_ENV_REQUIRED_KEYS}
    values["SOC_PINGAN_MODEL_GATEWAY_RSA_PRIVATE_KEY_FILE"] = (
        "${SOC_REPO_ROOT}/.secrets/eagw-private-key.der"
    )
    values.update(
        {
            "PINGAN_MODEL_GATEWAY_BASE_URL": "http://127.0.0.1:4001/v1",
            "PINGAN_MODEL_GATEWAY_API_KEY": "loopback-test-key",
            "PINGAN_MODEL_GATEWAY_MODEL": "deepseek-v4-flash",
            "SOC_PINGAN_MODEL_GATEWAY_ENABLED": "true",
            "SOC_PINGAN_MODEL_GATEWAY_HOST": "127.0.0.1",
            "SOC_PINGAN_MODEL_GATEWAY_PORT": "4001",
            "SOC_PINGAN_MODEL_GATEWAY_API_KEYS": "loopback-test-key",
            "SOC_PINGAN_MODEL_GATEWAY_MODEL_ALIAS": "deepseek-v4-flash",
            "SOC_PINGAN_MODEL_GATEWAY_PROVIDER": "eagw",
            "SOC_ANALYZER_MODE": "llm",
            "SOC_LLM_MODEL": "deepseek-v4-flash",
            "SOC_PINGAN_COMPAT_ENABLED": "true",
            "SOC_PINGAN_COMPAT_HOST": "0.0.0.0",
            "SOC_PINGAN_COMPAT_PORT": "8090",
            "SOC_PINGAN_COMPAT_APP_KEYS_JSON": '{"common":"compat-test-key"}',
            "SOC_PINGAN_LEGACY_LIFECYCLE_MODE": "fake",
            "SOC_PINGAN_LEGACY_CALLBACK_MODE": "fake",
            "SOC_PINGAN_LEGACY_WORKER_AUTO_MIGRATE": "false",
            "SOC_PINGAN_ENV": "dev",
        }
    )
    values.update(overrides or {})
    env_path = root / ".env.soc-dev.local"
    env_path.write_text(
        "".join(f'export {name}="{value}"\n' for name, value in sorted(values.items()))
        + extra,
        encoding="utf-8",
    )
    config_path = root / "config.pingan-dev.local"
    config_path.write_text(config_text, encoding="utf-8")
    key_path = root / ".secrets/eagw-private-key.der"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(b"test-private-key")
    env_path.chmod(0o600)
    config_path.chmod(0o600)
    key_path.chmod(0o600)
