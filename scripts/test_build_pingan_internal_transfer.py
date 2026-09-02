from __future__ import annotations

import io
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
from scripts.build_pingan_internal_transfer import (
    ARCHIVE_ROOT,
    PRIVATE_ENV_REQUIRED_KEYS,
    PRIVATE_OVERLAY_PATHS,
    REQUIRED_HANDOFF_SOURCE_PATHS,
    TRANSFER_INSTALLER_NAME,
    TRANSFER_RUNBOOK_NAME,
    _archive_manifest,
    _assert_private_overlay_config_ready,
    _assert_required_handoff_sources,
    _assert_source_freeze_allowed,
    _assert_source_path_safe,
    _sha256_file,
    _transfer_installer,
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
    assert "backend/scripts/soc_pingan_zeus_lifecycle_smoke.py" in required
    assert "backend/scripts/soc_pingan_zeus_lifecycle_response_probe.py" in required
    assert (
        "backend/soc_agent/integrations/pingan/legacy_compat/lifecycle_smoke.py"
        in required
    )
    assert "backend/scripts/soc_pingan_prepare_legacy_live_request.py" in required
    assert "backend/scripts/soc_pingan_set_legacy_provider_mode.py" in required
    assert "backend/scripts/soc_pingan_set_runtime_environment.py" in required
    assert (
        "backend/scripts/soc_pingan_prepare_legacy_model_gateway_profile.py" in required
    )
    assert "backend/soc_agent/integrations/pingan/zeus_target.py" in required
    assert "backend/soc_agent/integrations/pingan/runtime_environment.py" in required
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
        installer={
            "path": "/tmp/INSTALL-PINGAN-MAC.sh",
            "sha256": "installer-sha",
        },
    )

    assert "abc123" in runbook
    assert "source-sha" in runbook
    assert "private-sha" in runbook
    assert "installer-sha" in runbook
    assert TRANSFER_INSTALLER_NAME in runbook
    assert TRANSFER_RUNBOOK_NAME in runbook
    assert (
        "$HOME/Downloads/source/full_alert_2026_month_forth_sample_200.pkl" in runbook
    )
    assert "$HOME/Downloads/corpus/full_alert_dams_labeled_merged.pkl" in runbook
    assert "soc_pingan_stage_internal_corpus.py --apply" in runbook
    assert f'bash "$HOME/READY-TO-TRANSFER/{TRANSFER_INSTALLER_NAME}"' in runbook
    clean_install = runbook.split(
        "## 3. Install Or Data-Preserving Redeploy", maxsplit=1
    )[1].split("## 4. Stage Existing Corpus", maxsplit=1)[0]
    routine_install = clean_install.split("### 3.1 Stateless DEV Reset", maxsplit=1)[0]
    assert "soc_pingan_macos_host_dev.py stop" not in routine_install
    assert 'rm -rf "$TARGET_REPO"' not in routine_install
    assert "exit 1" not in clean_install
    assert "### 3.1 Stateless DEV Reset / 无状态 DEV 清洁重装" in runbook
    assert 'TARGET_REPO="$HOME/deer-flow"' in clean_install
    assert "DELETE-OLD-DEV" in clean_install
    assert (
        "Type DELETE-OLD-DEV to permanently remove this stateless DEV deployment: "
        "' confirmation </dev/tty" in clean_install
    )
    assert "for port in 3000 8001 2026 4001 8090" in clean_install
    assert '/bin/rm -rf "$TARGET_REPO"' in clean_install
    assert "旧 SQLite、Memory、账号和内网验收结果都会永久删除" in clean_install
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
    assert "soc_pingan_zeus_lifecycle_smoke.py" in runbook
    assert "soc_pingan_zeus_lifecycle_response_probe.py" in runbook
    assert "lifecycle-response.local.json" in runbook
    assert runbook.index("soc_pingan_zeus_lifecycle_smoke.py") < runbook.index(
        "soc_pingan_legacy_live_acceptance.py"
    )
    assert "provider_code=200" in runbook
    assert "旧失败 Job 不能通过 `--resume-existing`" in runbook
    assert "ZEUS 上游真实发起" in runbook
    live_acceptance = runbook.split("只有要验证真实旧 ZEUS", maxsplit=1)[1].split(
        "单条真实验收结束", maxsplit=1
    )[0]
    assert '--database-url "$SOC_DATABASE_URL"' in live_acceptance
    assert "--resume-existing" in live_acceptance
    assert "resumed_existing_confirmed=true" in live_acceptance
    assert "soc_pingan_prepare_legacy_live_request.py" in runbook
    assert "soc_pingan_set_legacy_provider_mode.py" in runbook
    assert "手工编辑请求 JSON" in runbook
    assert "编辑该文件并替换全部 placeholder" not in runbook
    assert (
        "cp backend/samples/pingan_dev/legacy-task-request.example.json" not in runbook
    )
    assert "soc_pingan_prepare_legacy_model_gateway_profile.py" in runbook
    assert "所有共享 ZEUS Provider 明确指向" in runbook
    assert "isec-gw.paic.com.cn" in runbook
    assert ".secrets/eagw-private-key.der" in runbook
    local_env_blocks = [
        block
        for block in re.findall(r"```bash\n(.*?)```", runbook, flags=re.DOTALL)
        if "source ./.env.soc-dev.local" in block
    ]
    assert len(local_env_blocks) == 5
    for block in local_env_blocks:
        assert 'export TARGET_REPO="$HOME/deer-flow"' in block
        assert 'cd "$TARGET_REPO"' in block
        assert (
            'eval "$(backend/.venv/bin/python '
            'backend/scripts/soc_pingan_local_paths.py --shell)"' in block
        )
    repository_blocks = [
        block
        for block in re.findall(r"```bash\n(.*?)```", runbook, flags=re.DOTALL)
        if any(
            marker in block
            for marker in (
                "python3.12 scripts/",
                "backend/.venv/bin/",
                "source ./.env.soc-dev.local",
                ".env.soc-dev.local config.pingan-dev.local",
            )
        )
    ]
    assert repository_blocks
    for block in repository_blocks:
        assert 'export TARGET_REPO="$HOME/deer-flow"' in block
        assert 'cd "$TARGET_REPO"' in block
    report_block = next(
        block
        for block in re.findall(r"```bash\n(.*?)```", runbook, flags=re.DOTALL)
        if 'cat "transfer-report-' in block
    )
    assert 'cd "$HOME/READY-TO-TRANSFER"' in report_block
    assert "proves_real_internal_connectivity=true" in runbook
    assert "task-request.local.json" in runbook
    assert 'shasum -a 256 \\\n  "deer-flow-pingan-source' in runbook
    assert "soc_pingan_model_gateway_smoke.py \\\n  --confirm-live" in runbook
    assert '--database-url "$SOC_DATABASE_URL"' in runbook
    host_install = runbook.split("## 5. Host Check And Install", maxsplit=1)[1].split(
        "## 6. Execution Plane Preflight", maxsplit=1
    )[0]
    assert "soc_agent.cli db upgrade" not in host_install
    assert "Host DEV `start` 统一负责 SOC SQLite migration" in host_install
    assert "新空库发生一次瞬时 `disk I/O error`" in host_install
    assert "不要重复执行已经通过的阶段" in host_install
    assert "不再建库或重启，直接执行模型 Smoke/后续验收" in host_install
    assert "SOC database preparation failed before sidecar startup" in host_install
    assert "## 7. Start Host DEV / 启动服务" in runbook
    assert "首次按本 Runbook 顺序执行到这里时，Host DEV 尚未启动" in runbook
    assert "只有本节曾经执行过、终端中断后回来继续验收时" in runbook
    assert "不要重复 `start`" in runbook
    assert "只需要页面演示或本地研判时在此停止" in runbook
    assert "不需要提供告警 ID，也不要切换 internal Provider" in runbook
    assert "SOC 数据库后来被删除或重建" in runbook
    assert "不得复用" in runbook
    assert "确认 Core 全部为 `true`、三个 Sidecar 都为 `running`" in runbook
    assert "首次提交这份\n新请求" in runbook
    assert "`soc_database.status=ready`" in runbook
    assert "`soc_database.schema_revision=0027_processing_jobs`" in runbook
    assert all("unset SOC_DATABASE_URL" not in block for block in local_env_blocks)
    assert (
        "正常重部署不要删除 `deerflow.db`、`soc_agent_dev.db` 或 "
        "`soc_agent_stg.db`" in runbook
    )
    assert "## 8. Promote Runtime To STG / 切换到 STG" in runbook
    assert "soc_pingan_set_runtime_environment.py" in runbook
    assert "--environment stg" in runbook
    assert "soc_agent_stg.db" in runbook
    assert "项目 STG -> ZEUS STG" in runbook
    assert "zeus_target_environment=stg" in runbook
    assert "zeus_target_matches_runtime=true" in runbook
    assert "service_mode=production_optimized" in runbook
    assert "原生 `--prod`" in runbook
    stg_section = runbook.split(
        "## 8. Promote Runtime To STG / 切换到 STG", maxsplit=1
    )[1].split("## 9. Real Integration Follow-up", maxsplit=1)[0]
    stg_command = re.findall(r"```bash\n(.*?)```", stg_section, flags=re.DOTALL)[0]
    assert "--demo-no-auth" not in stg_command
    assert "--mode fake" in stg_section


def test_transfer_installer_is_self_contained_and_orders_destructive_steps() -> None:
    installer = _transfer_installer(
        archives={
            "source": {
                "path": "/tmp/deer-flow-pingan-source-20260824T000000Z.tar.gz",
                "sha256": "source-sha",
            },
            "private_overlay": {
                "path": (
                    "/tmp/deer-flow-pingan-private-overlay-20260824T000000Z.tar.gz"
                ),
                "sha256": "private-sha",
            },
        }
    )

    assert installer.startswith("#!/usr/bin/env bash\n")
    assert '[[ "${BASH_SOURCE[0]}" != "$0" ]]' in installer
    assert "set -euo pipefail" in installer
    assert 'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")"' in installer
    assert 'TRANSFER_DIR="$SCRIPT_DIR"' in installer
    assert 'TARGET_REPO="$HOME/deer-flow"' in installer
    assert "source-sha" in installer
    assert "private-sha" in installer
    assert "for port in 3000 8001 2026 4001 8090" in installer
    assert installer.index("verify_archive") < installer.index("tar -xzf")
    assert installer.index("tar -xzf") < installer.index(
        "soc_pingan_macos_host_dev.py stop"
    )
    assert installer.index("soc_pingan_macos_host_dev.py stop") < installer.index(
        'mv -- "$TARGET_REPO" "$BACKUP_REPO"'
    )
    assert installer.index('mv -- "$TARGET_REPO" "$BACKUP_REPO"') < installer.index(
        'mv -- "$STAGED_REPO" "$TARGET_REPO"'
    )
    assert '"backend/.deer-flow/data"' in installer
    assert '"backend/.deer-flow/.jwt_secret"' in installer
    assert '"backend/.deer-flow/users"' in installer
    assert installer.index('mv -- "$STAGED_REPO" "$TARGET_REPO"') < installer.index(
        "if ! restore_persistent_state"
    )
    assert installer.index("if ! restore_persistent_state") < installer.index(
        'rm -rf -- "$BACKUP_REPO"'
    )
    assert '"$TARGET_REPO/.env.soc-dev.local"' in installer
    assert '"$TARGET_REPO/.secrets/eagw-private-key.der"' in installer


def test_transfer_installer_rejects_source_without_exiting_parent_shell(
    tmp_path: Path,
) -> None:
    installer_path = tmp_path / TRANSFER_INSTALLER_NAME
    installer_path.write_text(
        _transfer_installer(
            archives={
                "source": {"path": "/tmp/source.tar.gz", "sha256": "source-sha"},
                "private_overlay": {
                    "path": "/tmp/private.tar.gz",
                    "sha256": "private-sha",
                },
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; status=$?; printf "parent-survived:%s\\n" "$status"',
            "installer-source-test",
            str(installer_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "parent-survived:2" in completed.stdout
    assert "do not source this installer" in completed.stderr


def test_transfer_installer_replaces_checkout_without_parent_shell_state(
    tmp_path: Path,
) -> None:
    transfer_dir = tmp_path / "ready"
    transfer_dir.mkdir()
    source_path = transfer_dir / "source.tar.gz"
    private_path = transfer_dir / "private.tar.gz"
    _write_installer_fixture_archive(
        source_path,
        {
            "scripts/soc_pingan_macos_host_dev.py": "# staged host driver\n",
            "source-marker.txt": "new checkout\n",
        },
    )
    _write_installer_fixture_archive(
        private_path,
        {
            ".env.soc-dev.local": "export TEST=1\n",
            "config.pingan-dev.local": "config_version: 38\n",
            ".secrets/eagw-private-key.der": "private-key",
            "backend/.deer-flow/pingan-context/catalog.txt": "new catalog\n",
        },
    )
    installer_path = transfer_dir / TRANSFER_INSTALLER_NAME
    installer_path.write_text(
        _transfer_installer(
            archives={
                "source": {
                    "path": str(source_path),
                    "sha256": _sha256_file(source_path),
                },
                "private_overlay": {
                    "path": str(private_path),
                    "sha256": _sha256_file(private_path),
                },
            }
        ),
        encoding="utf-8",
    )

    home = tmp_path / "home"
    old_repo = home / "deer-flow"
    (old_repo / "scripts").mkdir(parents=True)
    (old_repo / "scripts/soc_pingan_macos_host_dev.py").write_text(
        "# old host driver\n",
        encoding="utf-8",
    )
    (old_repo / "old-marker.txt").write_text("old checkout\n", encoding="utf-8")
    (old_repo / "backend/.deer-flow/data").mkdir(parents=True)
    (old_repo / "backend/.deer-flow/data/deerflow.db").write_text(
        "deerflow-state\n",
        encoding="utf-8",
    )
    (old_repo / "backend/.deer-flow/data/soc_agent_dev.db").write_text(
        "soc-state\n",
        encoding="utf-8",
    )
    (old_repo / "backend/.deer-flow/.jwt_secret").write_text(
        "stable-secret\n",
        encoding="utf-8",
    )
    (old_repo / "backend/.deer-flow/users/alice").mkdir(parents=True)
    (old_repo / "backend/.deer-flow/users/alice/memory.json").write_text(
        '{"memory": "keep"}\n',
        encoding="utf-8",
    )
    (old_repo / "backend/.deer-flow/pingan-context").mkdir(parents=True)
    (old_repo / "backend/.deer-flow/pingan-context/catalog.txt").write_text(
        "stale catalog\n",
        encoding="utf-8",
    )
    (old_repo / "backend/.deer-flow/internal-host-dev").mkdir(parents=True)
    (old_repo / "backend/.deer-flow/internal-host-dev/stale.pid").write_text(
        "123\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3.12",
        '#!/bin/sh\n: > "$HOME/old-host-stopped"\n',
    )
    _write_executable(fake_bin / "lsof", "#!/bin/sh\nexit 1\n")
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TARGET_REPO": str(tmp_path / "stale-shell-target"),
        }
    )

    completed = subprocess.run(
        ["bash", str(installer_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert (home / "old-host-stopped").is_file()
    assert (home / "deer-flow/source-marker.txt").read_text() == "new checkout\n"
    assert not (home / "deer-flow/old-marker.txt").exists()
    assert (
        home / "deer-flow/backend/.deer-flow/data/deerflow.db"
    ).read_text() == "deerflow-state\n"
    assert (
        home / "deer-flow/backend/.deer-flow/data/soc_agent_dev.db"
    ).read_text() == "soc-state\n"
    assert (
        home / "deer-flow/backend/.deer-flow/.jwt_secret"
    ).read_text() == "stable-secret\n"
    assert (
        home / "deer-flow/backend/.deer-flow/users/alice/memory.json"
    ).read_text() == '{"memory": "keep"}\n'
    assert (
        home / "deer-flow/backend/.deer-flow/pingan-context/catalog.txt"
    ).read_text() == "new catalog\n"
    assert not (
        home / "deer-flow/backend/.deer-flow/internal-host-dev/stale.pid"
    ).exists()
    assert (home / "deer-flow/.env.soc-dev.local").stat().st_mode & 0o777 == 0o600
    assert (
        home / "deer-flow/.secrets/eagw-private-key.der"
    ).stat().st_mode & 0o777 == 0o600


def test_transfer_installer_hash_failure_preserves_old_checkout(tmp_path: Path) -> None:
    transfer_dir = tmp_path / "ready"
    transfer_dir.mkdir()
    source_path = transfer_dir / "source.tar.gz"
    private_path = transfer_dir / "private.tar.gz"
    _write_installer_fixture_archive(
        source_path,
        {"scripts/soc_pingan_macos_host_dev.py": "# staged host driver\n"},
    )
    _write_installer_fixture_archive(
        private_path,
        {
            ".env.soc-dev.local": "export TEST=1\n",
            "config.pingan-dev.local": "config_version: 38\n",
            ".secrets/eagw-private-key.der": "private-key",
        },
    )
    installer_path = transfer_dir / TRANSFER_INSTALLER_NAME
    installer_path.write_text(
        _transfer_installer(
            archives={
                "source": {"path": str(source_path), "sha256": "wrong-sha"},
                "private_overlay": {
                    "path": str(private_path),
                    "sha256": _sha256_file(private_path),
                },
            }
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    old_repo = home / "deer-flow"
    (old_repo / "scripts").mkdir(parents=True)
    (old_repo / "scripts/soc_pingan_macos_host_dev.py").write_text(
        "# old host driver\n",
        encoding="utf-8",
    )
    (old_repo / "old-marker.txt").write_text("old checkout\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3.12",
        '#!/bin/sh\n: > "$HOME/old-host-stopped"\n',
    )
    _write_executable(fake_bin / "lsof", "#!/bin/sh\nexit 1\n")
    env = dict(os.environ)
    env.update({"HOME": str(home), "PATH": f"{fake_bin}:{env['PATH']}"})

    completed = subprocess.run(
        ["bash", str(installer_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode != 0
    assert "SHA-256 mismatch" in completed.stderr
    assert (old_repo / "old-marker.txt").is_file()
    assert not (home / "old-host-stopped").exists()


def test_private_overlay_config_accepts_current_dynamic_profile(tmp_path: Path) -> None:
    _write_private_profiles(tmp_path)

    _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_rejects_non_dev_prd_active_zeus_target(
    tmp_path: Path,
) -> None:
    _write_private_profiles(
        tmp_path,
        overrides={
            "SOC_PINGAN_ZEUS_ENV": "stg",
            "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw-stg.paic.com.cn",
            "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw-stg.paic.com.cn",
            "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": "CALL_PINGAN_STG",
        },
    )

    with pytest.raises(ValueError, match="safe transfer profile"):
        _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_requires_both_governed_zeus_profiles(
    tmp_path: Path,
) -> None:
    _write_private_profiles(tmp_path)
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "\n".join(
            line
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("export SOC_PINGAN_ZEUS_STG_APP_KEY=")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="SOC_PINGAN_ZEUS_STG_APP_KEY"):
        _assert_private_overlay_config_ready(tmp_path)


def test_private_overlay_config_requires_active_dev_target_to_match_prd_profile(
    tmp_path: Path,
) -> None:
    _write_private_profiles(
        tmp_path,
        overrides={"SOC_PINGAN_ZEUS_APP_KEY": "different-active-key"},
    )

    with pytest.raises(ValueError, match="active ZEUS target"):
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
            "SOC_PINGAN_ZEUS_PRD_BASE_URL": "https://isec-gw.paic.com.cn",
            "SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS": "isec-gw.paic.com.cn",
            "SOC_PINGAN_ZEUS_PRD_APP_ID": "SEC-MODEL",
            "SOC_PINGAN_ZEUS_PRD_APP_KEY": "zeus-prd-key",
            "SOC_PINGAN_ZEUS_STG_BASE_URL": "https://isec-gw-stg.paic.com.cn",
            "SOC_PINGAN_ZEUS_STG_ALLOWED_HOSTS": "isec-gw-stg.paic.com.cn",
            "SOC_PINGAN_ZEUS_STG_APP_ID": "SEC-MODEL",
            "SOC_PINGAN_ZEUS_STG_APP_KEY": "zeus-stg-key",
            "SOC_PINGAN_ZEUS_ENV": "prd",
            "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw.paic.com.cn",
            "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw.paic.com.cn",
            "SOC_PINGAN_ZEUS_APP_ID": "SEC-MODEL",
            "SOC_PINGAN_ZEUS_APP_KEY": "zeus-prd-key",
            "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": "CALL_PINGAN_ZEUS_PRD",
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


def _write_installer_fixture_archive(
    path: Path,
    files: dict[str, str],
) -> None:
    with tarfile.open(path, "w:gz") as handle:
        for relative, content in files.items():
            payload = content.encode()
            member = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{relative}")
            member.size = len(payload)
            member.mode = 0o600
            handle.addfile(member, io.BytesIO(payload))


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)
