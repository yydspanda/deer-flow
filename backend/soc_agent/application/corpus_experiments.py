"""Compose DEV experiments from the existing corpus, Runtime and durable jobs."""

import hashlib
import os
import platform
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from deerflow.config import get_app_config
from soc_agent.application.analysis import build_soc_analysis_service
from soc_agent.application.memory import build_soc_memory_lesson_draft_service, build_soc_memory_profile_registry
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.core.memory_draft_jobs import SocMemoryDraftJobService
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher
from soc_agent.demo.corpus_experiment_runtime import CorpusRuntimeExecutor
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.demo.corpus_workbench import CORPUS_WORKBENCH_ENVIRONMENT, SocCorpusWorkbenchService
from soc_agent.llm import SocLLMSettings
from soc_agent.utils.hashing import stable_hash


@dataclass(frozen=True)
class CorpusExperimentApplication:
    service: SocCorpusExperimentService
    dispatcher: CorpusExperimentDispatcher
    workbench: SocCorpusWorkbenchService
    defaults: SocAnalysisExecutionOptions
    full_flow_defaults: SocAnalysisExecutionOptions
    max_concurrency: int
    draft_jobs: SocMemoryDraftJobService | None = None


def build_corpus_experiment_application(*, repository: SqlAlchemyAlertRepository, workbench: SocCorpusWorkbenchService, settings: SocLLMSettings) -> CorpusExperimentApplication:
    controls = workbench.run_controls
    defaults = (controls.defaults if controls else SocAnalysisExecutionOptions()).model_copy(update={"tenant_policy_enabled": False, "tenant_policy_advisor_enabled": False, "tenant_policy_signal_providers_enabled": False})

    def configuration(options):
        if controls is not None:
            controls.validate_selection(options)
        return {**corpus_configuration_snapshot(settings, options), "plan_id": workbench.batch_plan_id}

    executor = CorpusRuntimeExecutor(
        repository=repository,
        load_payload=workbench.load_experiment_payload,
        analysis_factory=lambda round_: build_soc_analysis_service(
            repository,
            settings=settings,
            runtime_environment=CORPUS_WORKBENCH_ENVIRONMENT,
            pattern_observation_enabled=False,
            execute_authorized_actions=False,
            execution_options=round_.options,
            memory_record_ids=frozenset(entry.memory_id for entry in round_.memory_snapshot),
        ),
        profile_registry=build_soc_memory_profile_registry(),
    )
    service = SocCorpusExperimentService(repository=repository, execute=executor, configuration_provider=configuration, max_concurrency=settings.max_concurrency)
    drafts = SocMemoryDraftJobService(repository=repository, drafter_factory=lambda: build_soc_memory_lesson_draft_service(repository, settings=settings), max_concurrency=settings.max_concurrency)
    return CorpusExperimentApplication(
        service=service,
        dispatcher=CorpusExperimentDispatcher(service, draft_jobs=drafts, max_concurrency=settings.max_concurrency),
        workbench=workbench,
        defaults=defaults,
        full_flow_defaults=controls.defaults if controls else defaults,
        max_concurrency=settings.max_concurrency,
        draft_jobs=drafts,
    )


def corpus_configuration_snapshot(settings: SocLLMSettings, options: SocAnalysisExecutionOptions) -> dict:
    """Freeze behavior, not secrets or the adjustable throughput budget."""
    behavior_settings = {key: value for key, value in asdict(settings).items() if key not in {"max_concurrency", "requests_per_minute", "admission_timeout_seconds"}}
    resources = {"SOC_LLM_MAX_CONCURRENCY", "SOC_LLM_REQUESTS_PER_MINUTE", "SOC_LLM_ADMISSION_TIMEOUT_SECONDS"}
    prefixes = ("SOC_LLM_", "SOC_ROLE_", "SOC_NORMALIZATION_", "SOC_MEMORY_", "SOC_TENANT_", "SOC_DIRECT_", "SOC_PINGAN_SOFTWARE_PATH_")
    environment = {key: value for key, value in os.environ.items() if key.startswith(prefixes) and key not in resources}
    # Paths and endpoint credentials are not exported. Content hashes still detect
    # changes to operator-owned policy/knowledge catalogs between admitted jobs.
    files = {}
    for key, value in environment.items():
        if key.endswith(("_PATH", "_FILE")) and value:
            path = Path(value).expanduser()
            if path.is_file():
                files[key] = _file_hash(path)
    models = get_app_config().models
    return {
        "schema_version": "soc.corpus_configuration.v1",
        "settings": behavior_settings,
        "options": options.model_dump(mode="json"),
        "model_configuration_hash": stable_hash([model.model_dump(mode="json") for model in models]),
        "environment_hash": stable_hash(environment),
        "configured_file_hashes": files,
        "implementation_hash": _implementation_hash(),
        "hardware": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "logical_cpus": os.cpu_count(), "python": platform.python_version()},
        "external_action_execution": False,
    }


@lru_cache(maxsize=128)
def _cached_file_hash(path: str, size: int, mtime_ns: int) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _file_hash(path: Path) -> str:
    stat = path.stat()
    return _cached_file_hash(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=1)
def _implementation_hash() -> str:
    root = Path(__file__).resolve().parents[3]
    paths = []
    for directory in (root / "backend" / "soc_agent", root / "skills" / "public"):
        paths.extend(path for path in directory.rglob("*") if path.is_file() and path.suffix in {".py", ".md", ".json", ".yaml"})
    return stable_hash({str(path.relative_to(root)): _file_hash(path) for path in sorted(paths)})
