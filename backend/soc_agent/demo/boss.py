"""Boss Demo v0.1 launch manifest and isolated database helpers."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url

from soc_agent.demo.investigation import SocDemoInvestigationReport

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BOSS_DEMO_DATABASE_PATH = REPO_ROOT / "backend/.deer-flow/data/soc_boss_demo.db"
BOSS_DEMO_TASK_ID = "BD-01"
BOSS_DEMO_VERSION = "v0.1"


class SocBossDemoCapabilityBoundary(BaseModel):
    """One explicit real/mock/shadow boundary shown in the launch manifest."""

    capability: str
    mode: Literal["real", "deterministic", "fixture", "mock", "shadow_only", "disabled"]
    production_ready: bool
    disclosure: str


class SocBossDemoPrimaryInvestigation(BaseModel):
    """Stable identifiers and visible counts for the primary demo investigation."""

    sample_id: str
    run_id: str
    alert_id: str
    queue_id: str | None = None
    source_type: str | None = None
    domain_finding_count: int = 0
    action_evidence_count: int = 0
    correlation_match_count: int = 0
    relevant_memory_count: int = 0
    timeline_item_count: int = 0


class SocBossDemoManifest(BaseModel):
    """One browser-first launch result for Boss Demo v0.1."""

    schema_version: str = "soc.boss_demo_manifest.v1"
    stage_task_id: str = BOSS_DEMO_TASK_ID
    demo_version: str = BOSS_DEMO_VERSION
    status: Literal["ready", "failed"]
    scenario: Literal["apt"] = "apt"
    reset_applied: bool = False
    database_backend: str
    database_locator: str
    analyzer: dict[str, object]
    primary_investigation: SocBossDemoPrimaryInvestigation | None = None
    web_url: str
    review_context_api_url: str | None = None
    launch_commands: dict[str, str] = Field(default_factory=dict)
    capability_boundaries: list[SocBossDemoCapabilityBoundary] = Field(default_factory=list)
    operator_notes: list[str] = Field(default_factory=list)
    investigation_report: SocDemoInvestigationReport


def default_boss_demo_database_url() -> str:
    """Return the isolated local SQLite URL used by Boss Demo v0.1."""

    return f"sqlite:///{DEFAULT_BOSS_DEMO_DATABASE_PATH}"


def prepare_boss_demo_database(database_url: str, *, reset: bool) -> tuple[str, bool]:
    """Create the SQLite parent directory and optionally remove only its database file."""

    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        if reset:
            raise ValueError("--reset is supported only for an explicit SQLite Boss Demo database")
        return url.render_as_string(hide_password=True), False

    if not url.database or url.database == ":memory:":
        raise ValueError("Boss Demo requires a file-backed SQLite database")
    database_path = Path(url.database).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    reset_applied = False
    if reset and database_path.exists():
        database_path.unlink()
        reset_applied = True
    return str(database_path), reset_applied


def build_boss_demo_manifest(
    report: SocDemoInvestigationReport,
    *,
    database_url: str,
    database_locator: str,
    analyzer_mode: str,
    model_name: str | None,
    reset_applied: bool,
    web_base_url: str,
) -> SocBossDemoManifest:
    """Build a secret-safe launch manifest from the existing persistent demo report."""

    primary_result = report.results[0] if report.results else None
    primary = None
    if primary_result is not None:
        primary = SocBossDemoPrimaryInvestigation(
            sample_id=primary_result.sample_id,
            run_id=primary_result.run_id,
            alert_id=primary_result.alert_id,
            queue_id=primary_result.queue_id,
            source_type=primary_result.source_type,
            domain_finding_count=primary_result.domain_finding_count,
            action_evidence_count=primary_result.evidence_count,
            correlation_match_count=primary_result.correlation_match_count,
            relevant_memory_count=primary_result.relevant_memory_count,
            timeline_item_count=primary_result.timeline_item_count,
        )

    normalized_web_base = web_base_url.rstrip("/")
    queue_id = primary.queue_id if primary is not None else None
    run_id = primary.run_id if primary is not None else None
    url = make_url(database_url)
    database_backend = url.get_backend_name()
    database_env_value = database_url if database_backend == "sqlite" else "<set-SOC_DATABASE_URL-to-the-same-database>"
    quoted_database_env = shlex.quote(database_env_value)
    review_command = f"cd backend && uv run soc show {run_id} --database-url {shlex.quote(database_url)} --pretty" if run_id is not None and database_backend == "sqlite" else "cd backend && uv run soc list --pretty"
    tui_command = (
        f"cd backend && uv run soc chat tui --queue-id {queue_id} --lead-agent --database-url {shlex.quote(database_url)}" if queue_id is not None and database_backend == "sqlite" else "cd backend && uv run soc chat tui --lead-agent"
    )

    return SocBossDemoManifest(
        status="ready" if report.failed_count == 0 and primary is not None else "failed",
        reset_applied=reset_applied,
        database_backend=database_backend,
        database_locator=database_locator,
        analyzer={
            "mode": analyzer_mode,
            "model_name": model_name,
            "bounded_runtime_node": True,
            "silent_fallback_allowed": False,
        },
        primary_investigation=primary,
        web_url=(f"{normalized_web_base}/workspace/soc/alerts?run_id={run_id}" if run_id is not None else f"{normalized_web_base}/workspace/soc/alerts"),
        review_context_api_url=(f"{normalized_web_base}/api/soc/alerts/{run_id}/context" if run_id is not None else None),
        launch_commands={
            "start_full_stack": "./scripts/soc-boss-demo.sh start",
            "start_without_docker": f"SOC_DATABASE_URL={quoted_database_env} make dev",
            "review_context": review_command,
            "lead_agent_tui": tui_command,
        },
        capability_boundaries=_boss_demo_capability_boundaries(analyzer_mode),
        operator_notes=[
            "Open web_url after the full stack is running; the demo is a run-scoped alert result and does not manufacture a ReviewQueue task.",
            "Use --reset to rebuild only the isolated Boss Demo SQLite database.",
            "The launch manifest never silently changes an llm request to deterministic mode.",
            "Boss Demo readiness is not SOC Alpha completeness or production readiness.",
        ],
        investigation_report=report,
    )


def _boss_demo_capability_boundaries(analyzer_mode: str) -> list[SocBossDemoCapabilityBoundary]:
    analyzer_boundary = SocBossDemoCapabilityBoundary(
        capability="bounded_runtime_analysis",
        mode="real" if analyzer_mode == "llm" else "deterministic",
        production_ready=False,
        disclosure=(
            "Uses the configured DeerFlow chat model inside the fixed SOC Runtime node; live invocation alone is not production readiness."
            if analyzer_mode == "llm"
            else "Uses the deterministic analyzer so the demo is repeatable without model/network access."
        ),
    )
    return [
        SocBossDemoCapabilityBoundary(
            capability="alert_input",
            mode="fixture",
            production_ready=False,
            disclosure="Uses one sanitized PingAn APT fixture; it is not a live Zeus or Kafka event.",
        ),
        analyzer_boundary,
        SocBossDemoCapabilityBoundary(
            capability="runtime_persistence_alert_result",
            mode="real",
            production_ready=False,
            disclosure="Uses production SOC service/repository contracts and run-scoped investigation context with an isolated local SQLite database.",
        ),
        SocBossDemoCapabilityBoundary(
            capability="read_only_investigation_actions",
            mode="mock",
            production_ready=False,
            disclosure="Threat-intelligence and security-tag lookups use local in-memory mock adapters.",
        ),
        SocBossDemoCapabilityBoundary(
            capability="confirmed_memory",
            mode="fixture",
            production_ready=False,
            disclosure="A retrieval-enabled demo memory is seeded only to demonstrate the governed memory projection.",
        ),
        SocBossDemoCapabilityBoundary(
            capability="high_risk_response",
            mode="disabled",
            production_ready=False,
            disclosure="No IP block, endpoint isolation, auto-close, or other high-risk side effect is executed.",
        ),
        SocBossDemoCapabilityBoundary(
            capability="governed_disposition",
            mode="shadow_only",
            production_ready=False,
            disclosure="Disposition proposals and authorization enrichment cannot mutate the Runtime verdict or close a queue.",
        ),
    ]
