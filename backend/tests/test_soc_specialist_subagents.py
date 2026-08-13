from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from deerflow.config.subagents_config import CustomSubagentConfig, SubagentsAppConfig
from deerflow.subagents.registry import get_subagent_config
from soc_agent.agent_profile import SocLeadAgentProfileInstaller
from soc_agent.cli import main
from soc_agent.lead_agent import (
    SOC_LEAD_AGENT_DELEGATION_MIDDLEWARE,
    SocLeadAgentRuntimeConfigurationError,
    validate_soc_lead_agent_runtime_configuration,
)
from soc_agent.skills import (
    SOC_EMAIL_PHISHING_TRIAGE_SKILL,
    SOC_ENDPOINT_TRIAGE_SKILL,
    SOC_NETWORK_APT_TRIAGE_SKILL,
    SOC_WEB_APPLICATION_TRIAGE_SKILL,
)
from soc_agent.subagents import (
    SOC_EMAIL_SPECIALIST_NAME,
    SOC_ENDPOINT_SPECIALIST_NAME,
    SOC_NETWORK_SPECIALIST_NAME,
    SOC_SPECIALIST_SUBAGENT_NAMES,
    SOC_WEB_SPECIALIST_NAME,
    SocSpecialistSubagentConfigInstaller,
    build_soc_specialist_subagent_config_fragment,
    build_soc_specialist_subagent_configs,
)


def _write_config(path: Path, document: dict[str, object] | None = None) -> None:
    path.write_text(
        yaml.safe_dump(document or {"config_version": 24, "models": []}, sort_keys=False),
        encoding="utf-8",
    )


def test_soc_specialist_profiles_use_deerflow_native_contracts() -> None:
    profiles = build_soc_specialist_subagent_configs()

    assert tuple(profiles) == SOC_SPECIALIST_SUBAGENT_NAMES
    assert all(isinstance(profile, CustomSubagentConfig) for profile in profiles.values())
    for profile in profiles.values():
        assert profile.tools == []
        assert profile.skills == []
        assert set(profile.disallowed_tools or ()) >= {
            "task",
            "ask_clarification",
            "present_files",
            "write_file",
            "str_replace",
            "bash",
        }
        assert profile.model == "inherit"
        assert profile.disallowed_output_markers == ["<soc_action_proposal>"]
        assert profile.max_turns == 32
        assert profile.timeout_seconds == 300
        assert "advisory reasoning" in profile.system_prompt
        assert "cannot change a verdict" in profile.system_prompt
        assert "approved bounded runtime guidance" in profile.system_prompt
        assert "Do not load skill files" in profile.system_prompt
        assert "real configured detector hit" in profile.system_prompt
        assert "best current scenario, effect, impact" in profile.system_prompt

    assert SOC_NETWORK_APT_TRIAGE_SKILL in profiles[SOC_NETWORK_SPECIALIST_NAME].system_prompt
    assert SOC_ENDPOINT_TRIAGE_SKILL in profiles[SOC_ENDPOINT_SPECIALIST_NAME].system_prompt
    assert "EDR, HIDS" in profiles[SOC_ENDPOINT_SPECIALIST_NAME].description
    assert SOC_WEB_APPLICATION_TRIAGE_SKILL in profiles[SOC_WEB_SPECIALIST_NAME].system_prompt
    assert SOC_EMAIL_PHISHING_TRIAGE_SKILL in profiles[SOC_EMAIL_SPECIALIST_NAME].system_prompt


def test_soc_specialist_fragment_loads_through_deerflow_registry() -> None:
    fragment = build_soc_specialist_subagent_config_fragment()
    subagents = SubagentsAppConfig.model_validate(fragment["subagents"])

    for name in SOC_SPECIALIST_SUBAGENT_NAMES:
        resolved = get_subagent_config(name, app_config=subagents)
        assert resolved is not None
        assert resolved.name == name
        assert resolved.tools == []
        assert resolved.skills == []
        assert resolved.max_turns == 32
        assert resolved.disallowed_output_markers == ["<soc_action_proposal>"]


def test_soc_runtime_configuration_validator_accepts_managed_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    SocLeadAgentProfileInstaller().install(user_id="soc-user")
    subagents = SubagentsAppConfig.model_validate(build_soc_specialist_subagent_config_fragment()["subagents"])

    report = validate_soc_lead_agent_runtime_configuration(
        user_id="soc-user",
        app_config=subagents,
    )

    assert report["status"] == "ready"
    assert report["validated_specialists"] == list(SOC_SPECIALIST_SUBAGENT_NAMES)


def test_soc_runtime_configuration_validator_rejects_stale_middleware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    result = SocLeadAgentProfileInstaller().install(user_id="soc-user")
    config_path = Path(result.config_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["middlewares"].remove(SOC_LEAD_AGENT_DELEGATION_MIDDLEWARE)
    config_path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(
        SocLeadAgentRuntimeConfigurationError,
        match="governance middleware",
    ):
        validate_soc_lead_agent_runtime_configuration(
            user_id="soc-user",
            require_specialists=False,
        )


def test_soc_specialist_installer_dry_run_does_not_write(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    before = config_path.read_text(encoding="utf-8")

    result = SocSpecialistSubagentConfigInstaller().install(
        config_path=config_path,
        dry_run=True,
    )

    assert result.status == "dry_run"
    assert result.changed_agent_names == sorted(SOC_SPECIALIST_SUBAGENT_NAMES)
    assert config_path.read_text(encoding="utf-8") == before


def test_soc_specialist_installer_preserves_unrelated_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    existing = CustomSubagentConfig(
        description="Existing operator agent",
        system_prompt="Keep me",
        tools=[],
        skills=[],
    ).model_dump(mode="json", exclude_none=True)
    _write_config(
        config_path,
        {
            "config_version": 24,
            "models": [{"name": "model-a", "use": "example:model"}],
            "subagents": {"max_total_per_run": 3, "custom_agents": {"operator-agent": existing}},
            "operator_extension": {"keep": True},
        },
    )

    result = SocSpecialistSubagentConfigInstaller().install(config_path=config_path)
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert result.status == "updated"
    assert result.changed_agent_names == sorted(SOC_SPECIALIST_SUBAGENT_NAMES)
    assert loaded["models"][0]["name"] == "model-a"
    assert loaded["operator_extension"] == {"keep": True}
    assert loaded["subagents"]["max_total_per_run"] == 3
    assert loaded["subagents"]["custom_agents"]["operator-agent"] == existing
    assert set(SOC_SPECIALIST_SUBAGENT_NAMES) <= set(loaded["subagents"]["custom_agents"])

    unchanged = SocSpecialistSubagentConfigInstaller().install(config_path=config_path)
    assert unchanged.status == "unchanged"
    assert unchanged.changed_agent_names == []


def test_soc_specialist_installer_fails_atomically_on_collision(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "config_version": 24,
            "subagents": {
                "custom_agents": {
                    SOC_NETWORK_SPECIALIST_NAME: {
                        "description": "Operator-owned replacement",
                        "system_prompt": "Do not overwrite",
                    }
                }
            },
        },
    )
    before = config_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match=SOC_NETWORK_SPECIALIST_NAME):
        SocSpecialistSubagentConfigInstaller().install(config_path=config_path)

    assert config_path.read_text(encoding="utf-8") == before

    result = SocSpecialistSubagentConfigInstaller().install(
        config_path=config_path,
        overwrite=True,
    )
    assert result.status == "updated"
    assert result.overwritten_agent_names == [SOC_NETWORK_SPECIALIST_NAME]


def test_soc_specialist_cli_shows_and_dry_runs_config(tmp_path: Path, capsys) -> None:
    assert main(["agent", "subagents", "--pretty"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["subagents"]["custom_agents"]) == set(SOC_SPECIALIST_SUBAGENT_NAMES)

    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    assert (
        main(
            [
                "agent",
                "install-subagents",
                "--config",
                str(config_path),
                "--pretty",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run"
    assert report["agent_names"] == list(SOC_SPECIALIST_SUBAGENT_NAMES)


def test_soc_specialist_cli_doctor_preserves_user_scope(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def validate(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "schema_version": "soc.lead_agent_runtime_configuration.v1",
            "status": "ready",
        }

    monkeypatch.setattr(
        "soc_agent.cli.validate_soc_lead_agent_runtime_configuration",
        validate,
    )

    assert (
        main(
            [
                "agent",
                "doctor",
                "--user-id",
                "analyst-1",
                "--pretty",
            ]
        )
        == 0
    )

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ready"
    assert captured == {
        "require_specialists": True,
        "user_id": "analyst-1",
    }
