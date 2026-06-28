from __future__ import annotations

import ast
from pathlib import Path

from soc_agent.contracts import AlertInput
from soc_agent.core import (
    SocAgentChatService,
    SocAnalysisService,
    SocDaemonService,
    SocMemoryService,
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
    assert SocReviewService.__name__ == "SocReviewService"
    assert SocMemoryService.__name__ == "SocMemoryService"
    assert SocDaemonService.__name__ == "SocDaemonService"
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
