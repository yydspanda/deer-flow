from __future__ import annotations

import ast
from pathlib import Path

from soc_agent.contracts import AlertInput
from soc_agent.core import (
    SocAgentChatService,
    SocAnalysisService,
    SocAuthorizationEnrichmentService,
    SocAuthorizedActivityService,
    SocDaemonService,
    SocDispositionProposalService,
    SocGovernedContextService,
    SocMemoryService,
    SocNormalizationService,
    SocReviewService,
    SocServiceNotFoundError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOC_AGENT = PROJECT_ROOT / "soc_agent"


def test_contracts_do_not_import_runtime_layers() -> None:
    forbidden = {
        "soc_agent.api",
        "soc_agent.channels",
        "soc_agent.cli",
        "soc_agent.core",
        "soc_agent.daemon",
        "soc_agent.db",
        "soc_agent.ingestion",
        "soc_agent.memory",
        "soc_agent.normalizers",
        "soc_agent.pipeline",
        "soc_agent.policy",
        "soc_agent.queue",
        "soc_agent.tui",
        "soc_agent.tools",
    }

    for module in _python_files(SOC_AGENT / "contracts"):
        assert not (_imports(module) & forbidden), f"{module} imports a forbidden runtime layer"


def test_core_does_not_import_transport_layers() -> None:
    forbidden = {
        "soc_agent.api",
        "soc_agent.channels",
        "soc_agent.cli",
        "soc_agent.daemon",
        "soc_agent.ingestion",
        "soc_agent.tui",
    }

    for module in _python_files(SOC_AGENT / "core"):
        assert not (_imports(module) & forbidden), f"{module} imports a transport layer"


def test_pipeline_has_no_transport_or_infrastructure_imports() -> None:
    forbidden = {
        "argparse",
        "fastapi",
        "kafka",
        "psycopg",
        "sqlalchemy",
        "typer",
        "soc_agent.api",
        "soc_agent.channels",
        "soc_agent.cli",
        "soc_agent.daemon",
        "soc_agent.db",
        "soc_agent.ingestion",
        "soc_agent.tui",
    }

    for module in _python_files(SOC_AGENT / "pipeline"):
        assert not (_imports(module) & forbidden), f"{module} imports transport or infrastructure code"


def test_production_soc_agent_does_not_import_validation_code() -> None:
    for module in _python_files(SOC_AGENT):
        forbidden = {imported for imported in _imports(module) if imported == "validation" or imported.startswith("validation.")}
        assert not forbidden, f"{module} imports validation-only code: {sorted(forbidden)}"


def test_fact_reconstructor_does_not_know_vendor_role_aliases() -> None:
    source = (SOC_AGENT / "pipeline" / "fact_reconstructor.py").read_text(encoding="utf-8")
    forbidden_aliases = {"attack_sip", "alarm_sip", "str_attack_ip", "str_source_ip"}

    assert not {alias for alias in forbidden_aliases if alias in source}


def test_governed_context_lifecycle_is_vendor_neutral() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            SOC_AGENT / "contracts" / "governed_context.py",
            SOC_AGENT / "contracts" / "authorization.py",
            SOC_AGENT / "core" / "governed_context.py",
            SOC_AGENT / "core" / "authorized_activity.py",
            SOC_AGENT / "core" / "authorization_enrichment.py",
            SOC_AGENT / "authorization" / "repositories.py",
            SOC_AGENT / "authorization" / "query.py",
            SOC_AGENT / "authorization" / "matcher.py",
            SOC_AGENT / "core" / "disposition_proposal.py",
            SOC_AGENT / "disposition" / "repositories.py",
        )
    ).lower()

    assert not {value for value in {"pingan", "zeus", "work04", "1.1.1.1"} if value in source}


def test_db_does_not_import_runtime_or_transport_layers() -> None:
    forbidden = {
        "fastapi",
        "kafka",
        "typer",
        "soc_agent.api",
        "soc_agent.channels",
        "soc_agent.cli",
        "soc_agent.core",
        "soc_agent.daemon",
        "soc_agent.ingestion",
        "soc_agent.pipeline",
        "soc_agent.tui",
    }

    for module in _python_files(SOC_AGENT / "db"):
        assert not (_imports(module) & forbidden), f"{module} imports runtime or transport code"


def test_action_boundaries_live_under_actions_package() -> None:
    root_action_like_files = {file.name for file in SOC_AGENT.glob("*.py") if "adapter" in file.stem or "proposal" in file.stem}

    assert root_action_like_files == set()
    assert (SOC_AGENT / "actions" / "adapters.py").exists()
    assert (SOC_AGENT / "actions" / "mcp.py").exists()
    assert (SOC_AGENT / "actions" / "proposals.py").exists()


def test_cli_enters_business_logic_through_core_service() -> None:
    imports = _imports(SOC_AGENT / "cli.py")

    assert "soc_agent.core" in imports
    assert "soc_agent.core.runtime" not in imports
    assert "soc_agent.pipeline.analyzer" not in imports
    assert "soc_agent.pipeline.extractor" not in imports


def test_alert_input_contract_is_strict() -> None:
    assert AlertInput.model_config.get("extra") == "forbid"


def test_core_exports_planned_public_services() -> None:
    assert SocAnalysisService.__name__ == "SocAnalysisService"
    assert SocAuthorizedActivityService.__name__ == "SocAuthorizedActivityService"
    assert SocAuthorizationEnrichmentService.__name__ == "SocAuthorizationEnrichmentService"
    assert SocReviewService.__name__ == "SocReviewService"
    assert SocNormalizationService.__name__ == "SocNormalizationService"
    assert SocMemoryService.__name__ == "SocMemoryService"
    assert SocDaemonService.__name__ == "SocDaemonService"
    assert SocDispositionProposalService.__name__ == "SocDispositionProposalService"
    assert SocGovernedContextService.__name__ == "SocGovernedContextService"
    assert SocAgentChatService.__name__ == "SocAgentChatService"
    assert SocServiceNotFoundError.__name__ == "SocServiceNotFoundError"


def _python_files(path: Path) -> list[Path]:
    return [file for file in path.rglob("*.py") if "__pycache__" not in file.parts]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported
