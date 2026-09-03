from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from soc_agent.integrations.pingan.legacy_workflow_profile import (
    PingAnLegacyWorkflowProfileError,
    load_legacy_workflow_profile,
    load_legacy_workflow_profiles,
    prepare_legacy_workflow_env,
    sanitized_profile_json,
)

_TEST_SECRET = "legacy-test-secret"


def test_legacy_profile_is_extracted_statically_from_yhsys_branch(
    tmp_path: Path,
) -> None:
    source = _write_legacy_source(tmp_path)

    profile = load_legacy_workflow_profile(source)
    profiles = load_legacy_workflow_profiles(source)

    assert profile.base_url == "https://agent-prd.example"
    assert profile.allowed_host == "agent-prd.example"
    assert profile.app_id == "YHSYS"
    assert profile.operator == "WANGWENBIN520"
    assert profile.app_secret == _TEST_SECRET
    assert _TEST_SECRET not in repr(profile)
    assert profiles.stg_base_url == "https://agent-stg.example"
    assert profiles.stg_allowed_host == "agent-stg.example"


def test_legacy_profile_preparer_updates_private_env_without_secret_output(
    tmp_path: Path,
) -> None:
    source = _write_legacy_source(tmp_path)
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export KEEP_ME='yes'\nexport env_profile='LOCAL'\nexport SOC_PINGAN_WORKFLOW_OPERATOR='someone-else'\nexport SOC_PINGAN_WORKFLOW_RUNNER_IMPORT='legacy:run'\n",
        encoding="utf-8",
    )

    report = prepare_legacy_workflow_env(
        repo_root=tmp_path,
        source_path=source,
        env_path=env_path,
        apply=True,
    )
    rendered = env_path.read_text(encoding="utf-8")
    public_report = sanitized_profile_json(report)

    assert "export KEEP_ME='yes'" in rendered
    assert "SOC_PINGAN_WORKFLOW_ENV=prd" in rendered
    assert "SOC_PINGAN_WORKFLOW_APP_ID=YHSYS" in rendered
    assert f"SOC_PINGAN_WORKFLOW_APP_SECRET={_TEST_SECRET}" in rendered
    assert "SOC_PINGAN_WORKFLOW_PRD_BASE_URL=https://agent-prd.example" in rendered
    assert f"SOC_PINGAN_WORKFLOW_PRD_APP_SECRET={_TEST_SECRET}" in rendered
    assert "SOC_PINGAN_WORKFLOW_STG_BASE_URL=https://agent-stg.example" in rendered
    assert "SOC_PINGAN_WORKFLOW_STG_APP_SECRET" not in rendered
    assert "SOC_PINGAN_WORKFLOW_OPERATOR" not in rendered
    assert "SOC_PINGAN_WORKFLOW_RUNNER_IMPORT" not in rendered
    assert "env_profile" not in rendered
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert report["operator"] == "WANGWENBIN520"
    assert report["prd_credential_present"] is True
    assert report["stg_profile_ready"] is False
    assert report["stg_missing_keys"] == [
        "SOC_PINGAN_WORKFLOW_STG_APP_ID",
        "SOC_PINGAN_WORKFLOW_STG_APP_SECRET",
        "SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID",
        "SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID",
        "SOC_PINGAN_WORKFLOW_STG_USER_ID",
    ]
    assert report["secret_in_output"] is False
    assert _TEST_SECRET not in public_report
    assert json.loads(public_report)["active_target_environment"] == "prd"

    replay = prepare_legacy_workflow_env(
        repo_root=tmp_path,
        source_path=source,
        env_path=env_path,
        apply=True,
    )
    assert replay["changed"] is False


def test_legacy_profile_preparer_preserves_complete_operator_owned_stg_profile(
    tmp_path: Path,
) -> None:
    source = _write_legacy_source(tmp_path)
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export SOC_PINGAN_ENV=stg\n"
        "export SOC_PINGAN_WORKFLOW_STG_APP_ID=YHSYS-STG\n"
        "export SOC_PINGAN_WORKFLOW_STG_APP_SECRET=stg-secret\n"
        "export SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID=2087710\n"
        "export SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID=2087787\n"
        "export SOC_PINGAN_WORKFLOW_STG_USER_ID=2092332\n",
        encoding="utf-8",
    )

    report = prepare_legacy_workflow_env(
        repo_root=tmp_path,
        source_path=source,
        env_path=env_path,
        apply=True,
    )

    rendered = env_path.read_text(encoding="utf-8")
    assert report["stg_profile_ready"] is True
    assert report["active_target_environment"] == "stg"
    assert "SOC_PINGAN_WORKFLOW_ENV=stg" in rendered
    assert "SOC_PINGAN_WORKFLOW_BASE_URL=https://agent-stg.example" in rendered
    assert "SOC_PINGAN_WORKFLOW_APP_ID=YHSYS-STG" in rendered
    assert "SOC_PINGAN_WORKFLOW_APP_SECRET=stg-secret" in rendered


def test_legacy_profile_preparer_rejects_partial_stg_identity(tmp_path: Path) -> None:
    source = _write_legacy_source(tmp_path)
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export SOC_PINGAN_ENV=dev\nexport SOC_PINGAN_WORKFLOW_STG_APP_ID=YHSYS-STG\n",
        encoding="utf-8",
    )

    with pytest.raises(PingAnLegacyWorkflowProfileError, match="STG profile is partial"):
        prepare_legacy_workflow_env(
            repo_root=tmp_path,
            source_path=source,
            env_path=env_path,
        )


def test_legacy_profile_preparer_treats_sample_stg_placeholders_as_missing(
    tmp_path: Path,
) -> None:
    source = _write_legacy_source(tmp_path)
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export SOC_PINGAN_ENV=dev\n"
        "export SOC_PINGAN_WORKFLOW_STG_APP_ID='<agent-platform-stg-app-id>'\n"
        "export SOC_PINGAN_WORKFLOW_STG_APP_SECRET='<agent-platform-stg-app-secret>'\n"
        "export SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID='<terminal-workflow-id>'\n"
        "export SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID='<datacenter-workflow-id>'\n"
        "export SOC_PINGAN_WORKFLOW_STG_USER_ID='<user-workflow-id>'\n",
        encoding="utf-8",
    )

    report = prepare_legacy_workflow_env(
        repo_root=tmp_path,
        source_path=source,
        env_path=env_path,
        apply=True,
    )

    rendered = env_path.read_text(encoding="utf-8")
    assert report["stg_profile_ready"] is False
    assert report["stg_missing_keys"] == [
        "SOC_PINGAN_WORKFLOW_STG_APP_ID",
        "SOC_PINGAN_WORKFLOW_STG_APP_SECRET",
        "SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID",
        "SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID",
        "SOC_PINGAN_WORKFLOW_STG_USER_ID",
    ]
    assert "<agent-platform-stg" not in rendered
    assert "<terminal-workflow-id>" not in rendered
    assert "<datacenter-workflow-id>" not in rendered
    assert "<user-workflow-id>" not in rendered


def test_legacy_profile_rejects_source_without_yhsys(tmp_path: Path) -> None:
    source = tmp_path / "agent_config.py"
    source.write_text(
        'agent_base_url = "https://agent-stg.example"\napp_config = {"OTHER": {"app_sk": "secret"}}\n',
        encoding="utf-8",
    )

    with pytest.raises(PingAnLegacyWorkflowProfileError, match="exactly one"):
        load_legacy_workflow_profile(source)


def _write_legacy_source(root: Path) -> Path:
    source = root / "agent_config.py"
    source.write_text(
        "if ENV == 'STG':\n"
        "    agent_base_url = 'https://agent-stg.example'\n"
        "    app_config = {'OTHER': {'app_sk': 'other-secret'}}\n"
        "else:\n"
        "    agent_base_url = 'https://agent-prd.example/'\n"
        f"    app_config = {{'YHSYS': {{'app_sk': '{_TEST_SECRET}'}}}}\n",
        encoding="utf-8",
    )
    return source
