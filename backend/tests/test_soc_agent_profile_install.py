from __future__ import annotations

import json
from pathlib import Path

from deerflow.config.agents_config import load_agent_config, load_agent_soul
from deerflow.config.paths import get_paths
from soc_agent.agent_profile import SocLeadAgentProfileInstaller
from soc_agent.cli import main
from soc_agent.lead_agent import (
    SOC_LEAD_AGENT_APPROVAL_MIDDLEWARE,
    SOC_LEAD_AGENT_DELEGATION_MIDDLEWARE,
    SOC_LEAD_AGENT_REVIEW_CONTEXT_MIDDLEWARE,
)
from soc_agent.skills import SOC_ALERT_TRIAGE_SKILL, SOC_LEAD_AGENT_NAME


def _reset_deerflow_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)


def test_soc_profile_install_dry_run_does_not_write(tmp_path: Path, monkeypatch) -> None:
    _reset_deerflow_home(tmp_path, monkeypatch)

    result = SocLeadAgentProfileInstaller().install(user_id="soc-user", dry_run=True)

    assert result.status == "dry_run"
    assert result.dry_run is True
    assert result.user_id == "soc-user"
    assert not Path(result.config_path).exists()
    assert not Path(result.soul_path).exists()


def test_soc_profile_install_creates_deerflow_user_agent_profile(tmp_path: Path, monkeypatch) -> None:
    _reset_deerflow_home(tmp_path, monkeypatch)

    result = SocLeadAgentProfileInstaller().install(user_id="soc-user")

    assert result.status == "created"
    assert Path(result.config_path).exists()
    assert Path(result.soul_path).exists()
    cfg = load_agent_config(SOC_LEAD_AGENT_NAME, user_id="soc-user")
    assert cfg is not None
    assert cfg.name == SOC_LEAD_AGENT_NAME
    assert cfg.skills is not None
    assert SOC_ALERT_TRIAGE_SKILL in cfg.skills
    assert cfg.middlewares == [
        SOC_LEAD_AGENT_REVIEW_CONTEXT_MIDDLEWARE,
        SOC_LEAD_AGENT_DELEGATION_MIDDLEWARE,
        SOC_LEAD_AGENT_APPROVAL_MIDDLEWARE,
    ]
    assert "SOC Triage Agent" in (load_agent_soul(SOC_LEAD_AGENT_NAME, user_id="soc-user") or "")


def test_soc_profile_install_skips_existing_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    _reset_deerflow_home(tmp_path, monkeypatch)
    first = SocLeadAgentProfileInstaller().install(user_id="soc-user")
    Path(first.soul_path).write_text("custom soul", encoding="utf-8")

    result = SocLeadAgentProfileInstaller().install(user_id="soc-user")

    assert result.status == "skipped"
    assert Path(first.soul_path).read_text(encoding="utf-8") == "custom soul"


def test_soc_profile_install_overwrites_existing_profile(tmp_path: Path, monkeypatch) -> None:
    _reset_deerflow_home(tmp_path, monkeypatch)
    first = SocLeadAgentProfileInstaller().install(user_id="soc-user")
    Path(first.soul_path).write_text("stale soul", encoding="utf-8")

    result = SocLeadAgentProfileInstaller().install(user_id="soc-user", overwrite=True)

    assert result.status == "updated"
    assert "SOC Triage Agent" in Path(first.soul_path).read_text(encoding="utf-8")


def test_soc_profile_install_skips_legacy_shared_collision(tmp_path: Path, monkeypatch) -> None:
    _reset_deerflow_home(tmp_path, monkeypatch)
    legacy_dir = get_paths().agent_dir(SOC_LEAD_AGENT_NAME)
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.yaml").write_text(f"name: {SOC_LEAD_AGENT_NAME}\n", encoding="utf-8")

    result = SocLeadAgentProfileInstaller().install(user_id="soc-user")

    assert result.status == "skipped"
    assert "legacy shared storage" in result.message
    assert not Path(result.config_path).exists()


def test_soc_profile_install_cli_outputs_dry_run_result(tmp_path: Path, monkeypatch, capsys) -> None:
    _reset_deerflow_home(tmp_path, monkeypatch)

    assert main(["agent", "install-profile", "--user-id", "soc-user", "--dry-run"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_name"] == SOC_LEAD_AGENT_NAME
    assert payload["status"] == "dry_run"
    assert payload["dry_run"] is True
