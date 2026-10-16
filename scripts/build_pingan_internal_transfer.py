#!/usr/bin/env python3
"""Build separate source and private-overlay archives for PingAn internal DEV."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

if __package__:
    from .soc_pingan_compatibility_gate import run_compatibility_gate
else:
    from soc_pingan_compatibility_gate import run_compatibility_gate

ROOT = Path(__file__).resolve().parents[1]
TRANSFER_ROOT = ROOT / "backend/.deer-flow/internal-transfer"
DEFAULT_OUTPUT_DIR = TRANSFER_ROOT / "READY-TO-TRANSFER"
TRANSFER_INSTALLER_NAME = "INSTALL-PINGAN-MAC.sh"
TRANSFER_RUNBOOK_NAME = "PINGAN-INTERNAL-MAC-UPGRADE-RUNBOOK.md"
TRANSFER_REINITIALIZE_RUNBOOK_NAME = "PINGAN-INTERNAL-MAC-REINITIALIZE-RUNBOOK.md"
ARCHIVE_ROOT = "deer-flow-pingan-internal"
MANIFEST_SCHEMA_VERSION = "soc.pingan_internal_transfer_manifest.v1"

SOURCE_EXCLUDED_PREFIXES = (
    ".git/",
    ".history/",
    ".notes/archive/",
    ".opencode/",
    ".understand-anything/",
    "backend/.deer-flow/",
    "backend/.venv/",
    "docs/pr-evidence/",
    "frontend/node_modules/",
    "frontend/public/demo/threads/",
    "pr-build/",
    "validation/original_works/",
)
SOURCE_FORBIDDEN_NAMES = frozenset(
    {
        ".env.soc-dev.local",
        "config.pingan-dev.local",
        "config.yaml",
        "extensions_config.json",
    }
)
SOURCE_FORBIDDEN_SUFFIXES = frozenset(
    {".db", ".der", ".key", ".pem", ".pkl", ".sqlite", ".sqlite3", ".xlsx"}
)
PRIVATE_OVERLAY_PATHS = (
    ".env.soc-dev.local",
    ".secrets/eagw-private-key.der",
    "config.pingan-dev.local",
    "validation/original_works/raw_program/Deepseek_Qwen_32B_EDR_Analysis_Ignored_Paths_Sup (1).xlsx",
    "validation/compact_zeus/data/corpus/full_alert_validation_corpus.manifest.json",
    "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.manifest.json",
    "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json",
    "backend/.deer-flow/pingan-context/software-path-catalog.sqlite",
    "backend/.deer-flow/pingan-context/software-path-catalog.build-report.json",
)
HOST_DEV_PERSISTENT_PATHS = (
    "backend/.deer-flow/data",
    "backend/.deer-flow/.jwt_secret",
    "backend/.deer-flow/memory.json",
    "backend/.deer-flow/USER.md",
    "backend/.deer-flow/users",
    "backend/.deer-flow/agents",
    "backend/.deer-flow/threads",
    "backend/.deer-flow/integrations",
    "backend/.deer-flow/managed-subagents",
    "backend/.deer-flow/skills",
    "backend/.deer-flow/.retrieval",
    "backend/.deer-flow/soc-internal-validation",
)
PRIVATE_ENV_REQUIRED_KEYS = frozenset(
    {
        "PINGAN_MODEL_GATEWAY_BASE_URL",
        "PINGAN_MODEL_GATEWAY_API_KEY",
        "PINGAN_MODEL_GATEWAY_MODEL",
        "PINGAN_MODEL_GATEWAY_SMOKE_THINKING_ENABLED",
        "PINGAN_MODEL_GATEWAY_SMOKE_MAX_TOKENS",
        "SOC_PINGAN_MODEL_GATEWAY_ENABLED",
        "SOC_PINGAN_MODEL_GATEWAY_HOST",
        "SOC_PINGAN_MODEL_GATEWAY_PORT",
        "SOC_PINGAN_MODEL_GATEWAY_API_KEYS",
        "SOC_PINGAN_MODEL_GATEWAY_MODEL_ALIAS",
        "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_MODEL",
        "SOC_PINGAN_MODEL_GATEWAY_PROVIDER",
        "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_BASE_URL",
        "SOC_PINGAN_MODEL_GATEWAY_ALLOWED_HOSTS",
        "SOC_PINGAN_MODEL_GATEWAY_ALLOW_INSECURE_HTTP",
        "SOC_PINGAN_MODEL_GATEWAY_APP_KEY",
        "SOC_PINGAN_MODEL_GATEWAY_APP_SECRET",
        "SOC_PINGAN_MODEL_GATEWAY_SCENE_ID",
        "SOC_PINGAN_MODEL_GATEWAY_OPENAPI_CODE",
        "SOC_PINGAN_MODEL_GATEWAY_OPENAPI_CREDENTIAL",
        "SOC_PINGAN_MODEL_GATEWAY_RSA_PRIVATE_KEY_FILE",
        "SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY",
        "SOC_PINGAN_MODEL_GATEWAY_ADMISSION_TIMEOUT_SECONDS",
        "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_TIMEOUT_SECONDS",
        "SOC_PINGAN_MODEL_GATEWAY_MAX_REQUEST_BYTES",
        "SOC_ANALYZER_MODE",
        "SOC_LLM_MODEL",
        "SOC_LLM_THINKING_ENABLED",
        "SOC_LLM_MAX_CONCURRENCY",
        "SOC_LLM_CALL_TIMEOUT_SECONDS",
        "SOC_PINGAN_COMPAT_ENABLED",
        "SOC_PINGAN_COMPAT_HOST",
        "SOC_PINGAN_COMPAT_PORT",
        "SOC_PINGAN_COMPAT_APP_KEYS_JSON",
        "SOC_PINGAN_COMPAT_AUTO_MIGRATE",
        "SOC_PINGAN_COMPAT_MAX_REQUEST_BYTES",
        "SOC_PINGAN_LEGACY_QUEUE_TTL_SECONDS",
        "SOC_PINGAN_LEGACY_LIFECYCLE_MODE",
        "SOC_PINGAN_LEGACY_CALLBACK_MODE",
        "SOC_PINGAN_LEGACY_WORKER_CONCURRENCY",
        "SOC_PINGAN_LEGACY_POLL_INTERVAL_SECONDS",
        "SOC_PINGAN_LEGACY_WORKER_LEASE_SECONDS",
        "SOC_PINGAN_LEGACY_WORKER_MAX_ATTEMPTS",
        "SOC_PINGAN_LEGACY_WORKER_RETRY_BACKOFF_SECONDS",
        "SOC_PINGAN_LEGACY_CALLBACK_LEASE_SECONDS",
        "SOC_PINGAN_LEGACY_CALLBACK_MAX_ATTEMPTS",
        "SOC_PINGAN_LEGACY_CALLBACK_RETRY_BACKOFF_SECONDS",
        "SOC_PINGAN_LEGACY_WORKER_AUTO_MIGRATE",
        "SOC_PINGAN_ENV",
        "SOC_PINGAN_ASSET_PROVIDER_MODE",
        "SOC_PINGAN_ZEUS_PRD_BASE_URL",
        "SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS",
        "SOC_PINGAN_ZEUS_PRD_APP_ID",
        "SOC_PINGAN_ZEUS_PRD_APP_KEY",
        "SOC_PINGAN_ZEUS_STG_BASE_URL",
        "SOC_PINGAN_ZEUS_STG_ALLOWED_HOSTS",
        "SOC_PINGAN_ZEUS_STG_APP_ID",
        "SOC_PINGAN_ZEUS_STG_APP_KEY",
        "SOC_PINGAN_ZEUS_ENV",
        "SOC_PINGAN_ZEUS_BASE_URL",
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS",
        "SOC_PINGAN_ZEUS_APP_ID",
        "SOC_PINGAN_ZEUS_APP_KEY",
        "SOC_PINGAN_ZEUS_PRD_CONFIRMATION",
        "D12B_INVALID_ZEUS_APP_KEY",
        "D12B_TIMEOUT_SECONDS",
        "SOC_PINGAN_WORKFLOW_ENV",
        "SOC_PINGAN_WORKFLOW_BASE_URL",
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS",
        "SOC_PINGAN_WORKFLOW_APP_ID",
        "SOC_PINGAN_WORKFLOW_APP_SECRET",
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID",
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID",
        "SOC_PINGAN_WORKFLOW_USER_ID",
        "SOC_PINGAN_WORKFLOW_PRD_BASE_URL",
        "SOC_PINGAN_WORKFLOW_PRD_ALLOWED_HOSTS",
        "SOC_PINGAN_WORKFLOW_PRD_APP_ID",
        "SOC_PINGAN_WORKFLOW_PRD_APP_SECRET",
        "SOC_PINGAN_WORKFLOW_PRD_TERMINAL_ID",
        "SOC_PINGAN_WORKFLOW_PRD_DATACENTER_ID",
        "SOC_PINGAN_WORKFLOW_PRD_USER_ID",
        "SOC_PINGAN_WORKFLOW_STG_BASE_URL",
        "SOC_PINGAN_WORKFLOW_STG_ALLOWED_HOSTS",
    }
)
PRIVATE_ENV_OPTIONAL_AGENT_PLATFORM_STG_KEYS = frozenset(
    {
        "SOC_PINGAN_WORKFLOW_STG_APP_ID",
        "SOC_PINGAN_WORKFLOW_STG_APP_SECRET",
        "SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID",
        "SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID",
        "SOC_PINGAN_WORKFLOW_STG_USER_ID",
    }
)
PRIVATE_ENV_OBSOLETE_KEYS = frozenset(
    {
        "PINGAN_LITELLM_BASE_URL",
        "PINGAN_LITELLM_API_KEY",
        "PINGAN_LITELLM_MODEL",
        "env_profile",
        "SOC_PINGAN_PROVIDER_IMPORT_PATHS",
        "SOC_PINGAN_ZEUS_SIGNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_RUNNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_OPERATOR",
    }
)
_SHELL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONFIG_VERSION = re.compile(
    r"(?m)^\s*config_version\s*:\s*(?P<version>[0-9]+)\s*(?:#.*)?$"
)
REQUIRED_HANDOFF_SOURCE_PATHS = (
    ".notes/ai_soc/delivery-roadmap.md",
    ".notes/ai_soc/integrations/README.md",
    ".notes/ai_soc/integrations/mock-and-real-register.md",
    ".notes/ai_soc/integrations/pingan-dev-information-collection.md",
    ".notes/ai_soc/integrations/pingan-internal-continuation-handoff.md",
    ".notes/ai_soc/integrations/pingan-legacy-compatibility-execution-plane.md",
    "AGENTS.md",
    "backend/samples/pingan_dev/README.md",
    "backend/samples/pingan_dev/config.example.yaml",
    "backend/samples/pingan_dev/d12b-test-cases.example.yaml",
    "backend/samples/pingan_dev/env.example",
    "backend/samples/pingan_dev/extensions.example.json",
    "backend/samples/pingan_dev/legacy-task-request.example.json",
    "backend/samples/pingan_dev/uv-index.env.example",
    "backend/samples/enrichment/pingan-external-simulation.yaml",
    "backend/samples/enrichment/pingan-internal-shadow.yaml",
    "backend/samples/mcp/pingan_asset/action_adapters.json",
    "backend/samples/mcp/pingan_asset/extensions.internal.example.json",
    "backend/samples/mcp/pingan_asset/README.md",
    "backend/samples/mcp/pingan_shadow/extensions.internal.json",
    "backend/samples/mcp/pingan_threat_intel/action_adapters.json",
    "backend/samples/mcp/pingan_security_tag/action_adapters.json",
    "backend/scripts/soc_pingan_asset_direct_smoke.py",
    "backend/scripts/soc_pingan_d12b_evidence.py",
    "backend/scripts/soc_pingan_d12b_matrix.py",
    "backend/scripts/soc_pingan_dev_preflight.py",
    "backend/scripts/soc_pingan_local_paths.py",
    "backend/scripts/soc_pingan_model_gateway.py",
    "backend/scripts/soc_pingan_model_gateway_smoke.py",
    "backend/scripts/soc_pingan_legacy_api.py",
    "backend/scripts/soc_pingan_legacy_worker.py",
    "backend/scripts/soc_pingan_legacy_fake_acceptance.py",
    "backend/scripts/soc_pingan_zeus_lifecycle_smoke.py",
    "backend/scripts/soc_pingan_zeus_lifecycle_response_probe.py",
    "backend/scripts/soc_pingan_legacy_live_acceptance.py",
    "backend/scripts/soc_pingan_prepare_legacy_live_request.py",
    "backend/scripts/soc_pingan_set_legacy_provider_mode.py",
    "backend/scripts/soc_pingan_set_runtime_environment.py",
    "backend/scripts/soc_pingan_security_tag_mcp_server.py",
    "backend/scripts/soc_pingan_threat_intel_mcp_server.py",
    "backend/scripts/soc_pingan_prepare_legacy_model_gateway_profile.py",
    "backend/scripts/soc_pingan_prepare_legacy_workflow_profile.py",
    "backend/app/pingan_compat/__init__.py",
    "backend/app/pingan_compat/app.py",
    "backend/app/pingan_model_gateway/__init__.py",
    "backend/app/pingan_model_gateway/app.py",
    "backend/soc_agent/application/analysis.py",
    "backend/soc_agent/protocols.py",
    "backend/soc_agent/db/engine.py",
    "backend/soc_agent/db/models.py",
    "backend/soc_agent/integrations/pingan/agent_platform_target.py",
    "backend/soc_agent/integrations/pingan/agent_workflow.py",
    "backend/soc_agent/integrations/pingan/asset_location.py",
    "backend/soc_agent/integrations/pingan/legacy_model_gateway_profile.py",
    "backend/soc_agent/integrations/pingan/legacy_workflow_profile.py",
    "backend/soc_agent/integrations/pingan/model_gateway.py",
    "backend/soc_agent/integrations/pingan/model_gateway_smoke.py",
    "backend/soc_agent/integrations/pingan/memory/reference_retrieval.py",
    "backend/soc_agent/integrations/pingan/memory/service_identity.py",
    "backend/soc_agent/integrations/pingan/memory/process_facts.py",
    "backend/soc_agent/memory/http_behaviors.py",
    "backend/soc_agent/memory/projection_audit.py",
    "backend/soc_agent/normalizers/pingan_hids_targets.py",
    "backend/soc_agent/normalizers/process_facts.py",
    "backend/soc_agent/integrations/pingan/runtime_environment.py",
    "backend/soc_agent/integrations/pingan/zeus_target.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/acceptance.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/callback.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/contracts.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/execution.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/lifecycle_smoke.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/live_acceptance.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/provider_mode.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/request_preparation.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/result_mapper.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/service.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/worker.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/wiring.py",
    "backend/soc_agent/integrations/pingan/legacy_compat/zeus_lifecycle.py",
    "backend/soc_agent/contracts/processing_jobs.py",
    "backend/soc_agent/db/jobs.py",
    "backend/soc_agent/db/migrations/versions/0027_processing_jobs.py",
    "backend/soc_agent/db/migrations/versions/0028_corpus_list_projection.py",
    "backend/soc_agent/db/corpus_lists.py",
    "backend/soc_agent/db/migrations/versions/0029_processing_job_scope.py",
    "backend/soc_agent/db/migrations/versions/0030_corpus_experiments.py",
    "backend/soc_agent/db/migrations/versions/0031_memory_working_drafts.py",
    "backend/soc_agent/db/migrations/versions/0032_corpus_revision_index.py",
    "backend/soc_agent/db/migrations/versions/0033_pattern_observation_revisions.py",
    "backend/soc_agent/contracts/memory_drafts.py",
    "backend/soc_agent/core/memory_working_drafts.py",
    "backend/soc_agent/core/memory_draft_jobs.py",
    "backend/soc_agent/application/corpus_experiments.py",
    "backend/soc_agent/contracts/corpus_experiments.py",
    "backend/soc_agent/contracts/corpus_rebalance.py",
    "backend/soc_agent/contracts/corpus_rebalance_commands.py",
    "backend/soc_agent/db/corpus_experiments.py",
    "backend/soc_agent/demo/corpus_batches.py",
    "backend/soc_agent/demo/corpus_experiment_cli.py",
    "backend/soc_agent/demo/corpus_experiment_dispatcher.py",
    "backend/soc_agent/demo/corpus_capacity.py",
    "backend/soc_agent/demo/corpus_experiment_reports.py",
    "backend/soc_agent/demo/corpus_retest_selection.py",
    "backend/soc_agent/demo/corpus_round_comparison.py",
    "backend/soc_agent/demo/corpus_experiment_timing.py",
    "backend/soc_agent/demo/corpus_experiment_runtime.py",
    "backend/soc_agent/demo/corpus_pattern_continuation.py",
    "backend/soc_agent/demo/corpus_experiments.py",
    "backend/soc_agent/demo/corpus_rebalance.py",
    "backend/soc_agent/demo/corpus_rebalance_plan.py",
    "backend/soc_agent/demo/corpus_quick_validation.py",
    "backend/soc_agent/integrations/pingan/corpus_validation.py",
    "backend/app/gateway/routers/soc_corpus_experiments.py",
    "backend/app/gateway/soc_corpus_control.py",
    "backend/scripts/soc_corpus_batch_preview.py",
    "backend/scripts/soc_corpus_experiment.py",
    ".notes/ai_soc/integrations/pingan-corpus-batch-runbook.md",
    "frontend/scripts/soc-frontend.mjs",
    "backend/soc_agent/integrations/pingan/policies/tenant-disposition-v2.json",
    "backend/soc_agent/integrations/pingan/security_tag.py",
    "backend/soc_agent/integrations/pingan/threat_intel.py",
    "backend/soc_agent/contracts/tenant_policy.py",
    "backend/soc_agent/core/tenant_policy.py",
    "backend/soc_agent/tenant_policy/evaluator.py",
    "backend/soc_agent/tenant_policy/loader.py",
    "backend/soc_agent/db/migrations/versions/0022_tenant_policy_decisions.py",
    "scripts/build_pingan_macos_offline_bundle.py",
    "scripts/build_pingan_internal_transfer.py",
    "scripts/soc_pingan_compatibility_gate.py",
    "scripts/test_soc_pingan_compatibility_gate.py",
    "scripts/test_pingan_transfer_compatibility_gate.py",
    "backend/tests/test_soc_release_memory_compatibility.py",
    "backend/tests/test_soc_pingan_memory_profile_restore.py",
    "backend/tests/test_soc_memory_reference_retrieval.py",
    "backend/tests/test_soc_memory_behavior_reference.py",
    "backend/tests/test_soc_memory_service_identity.py",
    "backend/tests/test_soc_memory_http_behaviors.py",
    "backend/tests/test_soc_hids_command_targets.py",
    "backend/tests/test_soc_process_fact_contracts.py",
    "backend/tests/test_soc_normalization_process_facts.py",
    "backend/tests/test_soc_pingan_hids_deterministic_process.py",
    "backend/tests/test_soc_pingan_memory_process_facts.py",
    "backend/tests/test_soc_memory_projection_audit.py",
    "backend/tests/test_soc_pingan_hids_webshell.py",
    "backend/tests/test_soc_pingan_webshell_memory_regression.py",
    "backend/tests/test_soc_normalization_file_process_boundary.py",
    "backend/tests/test_soc_file_detection_extraction.py",
    "backend/tests/test_soc_hids_file_fallback_scope.py",
    "backend/tests/test_soc_network_behavior_observations.py",
    "backend/tests/test_soc_memory_reference_compatibility_safety.py",
    "backend/tests/test_soc_pingan_memory_service_direction.py",
    "backend/tests/test_soc_memory_revision_workflow.py",
    "backend/tests/test_soc_pingan_memory_profile.py",
    "backend/tests/test_soc_corpus_pattern_execution.py",
    "backend/tests/test_soc_corpus_experiment_cli.py",
    "backend/tests/test_soc_corpus_same_round_retry.py",
    "backend/tests/test_soc_corpus_retry_isolation.py",
    "backend/tests/test_soc_corpus_semantic_retry_runtime.py",
    "backend/tests/test_soc_corpus_continuous_learning.py",
    "backend/tests/test_soc_corpus_continuous_event_range.py",
    "backend/tests/test_soc_corpus_pattern_learning_display.py",
    "backend/tests/test_soc_corpus_pattern_continuation.py",
    "backend/tests/test_soc_pingan_pattern_capacity.py",
    "backend/tests/test_soc_memory_pattern_continuous_scope.py",
    "backend/tests/test_soc_memory_pattern_revisions.py",
    "backend/tests/test_soc_memory_pattern_current_views.py",
    "backend/tests/test_soc_corpus_rebalance_api.py",
    "backend/tests/test_soc_corpus_rebalance_cli.py",
    "backend/tests/test_soc_corpus_rebalance_execution.py",
    "backend/tests/test_soc_corpus_rebalance_plan.py",
    "backend/tests/test_soc_corpus_rebalance_reports.py",
    "backend/tests/test_soc_corpus_learning_first_reports.py",
    "backend/tests/test_soc_corpus_rebalance_repository.py",
    "backend/tests/test_soc_corpus_rebalance_service.py",
    "backend/tests/test_soc_fact_preparation.py",
    "backend/tests/test_soc_memory_experiment_retrieval.py",
    "backend/tests/test_soc_database_engine.py",
    "backend/tests/test_soc_corpus_revision_index.py",
    "backend/tests/test_soc_corpus_list_queries.py",
    "backend/tests/test_soc_corpus_capacity.py",
    "backend/tests/test_soc_corpus_capacity_integration.py",
    "backend/tests/test_soc_corpus_capacity_edges.py",
    "backend/tests/test_soc_sqlite_persistence_contention.py",
    "backend/tests/fixtures/soc_memory/profile7_v5_release_20260918.json",
    "scripts/build_pingan_corpus_transfer.py",
    "scripts/soc_pingan_macos_host_dev.py",
    "scripts/soc_pingan_host_sidecars.py",
    "scripts/soc_pingan_dev_database.py",
    "scripts/test_soc_pingan_dev_database.py",
    "scripts/soc_pingan_validation_database.py",
    "scripts/test_soc_pingan_validation_database.py",
    "scripts/soc_pingan_validation_report.py",
    "scripts/soc_memory_snapshot_audit.py",
    "scripts/test_soc_memory_snapshot_audit.py",
    "scripts/soc_bulk_review_learning.py",
    "scripts/test_soc_bulk_review_learning.py",
    "scripts/test_soc_bulk_review_incremental.py",
    "docs/soc-corpus-rebalance.md",
    "docs/soc-learning-review-batch.md",
    "scripts/soc_validation_report_data.py",
    "scripts/soc_validation_report_render.py",
    "scripts/test_soc_pingan_validation_report.py",
    "scripts/test_soc_validation_report_data.py",
    "scripts/test_soc_validation_report_render.py",
    "scripts/test_soc_validation_report_integration.py",
    "docs/soc-validation-effect-report.md",
    "docs/soc-corpus-operations-runbook.md",
    "scripts/soc_stage_report_summary.py",
    "scripts/test_soc_stage_report_summary.py",
    "backend/scripts/soc_validation_reset_store.py",
    "backend/tests/test_soc_validation_reset_store.py",
    "docs/soc-validation-reset.md",
    "docs/soc-validation-restart.md",
    "backend/tests/test_soc_corpus_validation_restart.py",
    "scripts/soc_pingan_stage_internal_corpus.py",
    "scripts/test_build_pingan_macos_offline_bundle.py",
    "scripts/test_soc_pingan_macos_host_dev.py",
    "scripts/test_soc_pingan_host_sidecars.py",
    "validation/compact_zeus/internal_batch/README.md",
    "validation/compact_zeus/internal_batch/evaluate_pingan_shadow.py",
    "validation/compact_zeus/internal_batch/run_pingan_internal_shadow.py",
    "validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py",
    "validation/compact_zeus/e2e/README.md",
    "validation/compact_zeus/e2e/run_ten_alert_e2e.py",
    "validation/compact_zeus/e2e/ten-alert-cases.json",
    "validation/compact_zeus/policy/README.md",
    "validation/compact_zeus/policy/validate_tenant_policy_shadow.py",
)


def collect_source_paths(root: Path = ROOT) -> list[Path]:
    """Collect current tracked and non-ignored untracked files from the worktree."""

    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        check=True,
        capture_output=True,
    )
    selected: list[Path] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        normalized = relative.as_posix()
        path = root / relative
        if _source_path_excluded(normalized):
            continue
        if not path.exists() and not path.is_symlink():
            continue
        if not path.is_file() and not path.is_symlink():
            continue
        _assert_source_path_safe(relative)
        selected.append(relative)
    return sorted(set(selected), key=lambda item: item.as_posix())


def build_transfer_archives(
    *,
    root: Path = ROOT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    include_private_overlay: bool = False,
    allow_dirty: bool = False,
    initialize_soc_dev: bool = False,
) -> dict[str, Any]:
    git_info = _git_info(root)
    _assert_source_freeze_allowed(git_info, allow_dirty=allow_dirty)
    output_dir = output_dir.expanduser().resolve()
    private_paths: list[Path] | None = None
    if include_private_overlay:
        private_paths = [Path(item) for item in PRIVATE_OVERLAY_PATHS]
        missing = [
            path.as_posix() for path in private_paths if not (root / path).is_file()
        ]
        if missing:
            raise ValueError(f"private overlay inputs are missing: {missing}")
        _assert_private_overlay_config_ready(root)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    source_paths = collect_source_paths(root)
    _assert_required_handoff_sources(source_paths)
    source_manifest = _archive_manifest(
        archive_kind="source",
        root=root,
        paths=source_paths,
        created_at=timestamp,
        git_info=git_info,
        private=False,
    )
    fingerprint = _source_fingerprint(source_manifest)
    compatibility = run_compatibility_gate(
        root, source_commit=git_info["commit"], source_fingerprint=fingerprint
    )
    if (
        compatibility.get("status") != "passed"
        or compatibility.get("source_commit") != git_info["commit"]
        or compatibility.get("source_fingerprint") != fingerprint
    ):
        raise ValueError("compatibility evidence does not match the frozen source")
    _assert_source_unchanged(root, source_manifest)
    # Keep prior verified archives and their installer/runbooks intact until
    # tests, archive inspection and the final source identity check all pass.
    with tempfile.TemporaryDirectory(prefix="pingan-verified-transfer-") as directory:
        staging = Path(directory)
        report = _write_transfer_artifacts(
            root=root,
            output_dir=staging,
            source_paths=source_paths,
            source_manifest=source_manifest,
            private_paths=private_paths,
            git_info=git_info,
            timestamp=timestamp,
            allow_dirty=allow_dirty,
            initialize_soc_dev=initialize_soc_dev,
            compatibility=compatibility,
        )
        for archive in report["archives"].values():
            inspect_archive(Path(archive["path"]))
        _assert_source_unchanged(root, source_manifest)
        _publish_transfer_artifacts(staging, output_dir, report)
    return report


def _source_fingerprint(manifest: MappingLike) -> str:
    frozen = {"commit": manifest["git"]["commit"], "files": manifest["files"]}
    return hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _assert_source_unchanged(root: Path, original: MappingLike) -> None:
    current_git = _git_info(root)
    if current_git != original["git"]:
        raise ValueError("source identity changed after compatibility checks started")
    current = _archive_manifest(
        archive_kind="source",
        root=root,
        paths=collect_source_paths(root),
        created_at=original["created_at"],
        git_info=current_git,
        private=False,
    )
    if _source_fingerprint(current) != _source_fingerprint(original):
        raise ValueError("source files changed after compatibility checks started")


def _write_transfer_artifacts(
    *,
    root: Path,
    output_dir: Path,
    source_paths: list[Path],
    source_manifest: MappingLike,
    private_paths: list[Path] | None,
    git_info: dict[str, Any],
    timestamp: str,
    allow_dirty: bool,
    initialize_soc_dev: bool,
    compatibility: dict[str, Any],
) -> dict[str, Any]:
    source_path = output_dir / f"deer-flow-pingan-source-{timestamp}.tar.gz"
    _write_archive(
        source_path,
        root=root,
        paths=source_paths,
        manifest=source_manifest,
        readme=_source_readme(timestamp, worktree_dirty=git_info["worktree_dirty"]),
    )
    archives: dict[str, Any] = {
        "source": _archive_result(source_path, source_manifest),
    }

    if private_paths is not None:
        private_manifest = _archive_manifest(
            archive_kind="private_overlay",
            root=root,
            paths=private_paths,
            created_at=timestamp,
            git_info=git_info,
            private=True,
        )
        private_path = (
            output_dir / f"deer-flow-pingan-private-overlay-{timestamp}.tar.gz"
        )
        _write_archive(
            private_path,
            root=root,
            paths=private_paths,
            manifest=private_manifest,
            readme=_private_readme(timestamp),
        )
        archives["private_overlay"] = _archive_result(
            private_path,
            private_manifest,
        )

    installer_path = output_dir / TRANSFER_INSTALLER_NAME
    _write_private_text(
        installer_path,
        _transfer_installer(archives=archives),
    )
    installer_path.chmod(0o700)
    installer = _sidecar_result(installer_path)

    summary_tool_path = output_dir / "soc_stage_report_summary.py"
    _write_private_text(
        summary_tool_path,
        Path(__file__)
        .with_name("soc_stage_report_summary.py")
        .read_text(encoding="utf-8"),
    )
    operator_tools = {"stage_report_summary": _sidecar_result(summary_tool_path)}

    report_path = output_dir / f"transfer-report-{timestamp}.json"
    runbooks = _write_transfer_runbooks(
        output_dir=output_dir,
        timestamp=timestamp,
        git_info=git_info,
        archives=archives,
        report_name=report_path.name,
        installer=installer,
    )
    default_runbook = "reinitialize" if initialize_soc_dev else "upgrade"
    report = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "output_directory": str(output_dir),
        "archives": archives,
        "installer": installer,
        "operator_tools": operator_tools,
        "runbook": runbooks[default_runbook],
        "runbooks": runbooks,
        "default_runbook": default_runbook,
        "source_worktree_dirty": git_info["worktree_dirty"],
        "required_source_file_count": len(REQUIRED_HANDOFF_SOURCE_PATHS),
        "required_source_inventory_complete": True,
        "dirty_override_used": bool(git_info["worktree_dirty"] and allow_dirty),
        "final_handoff_eligible": not git_info["worktree_dirty"]
        and compatibility["status"] == "passed",
        "compatibility_check": {
            **compatibility,
            "source_archive_sha256": archives["source"]["sha256"],
        },
        "reset_soc_dev_requested": initialize_soc_dev,
        "secrets_in_console_output": False,
    }
    _write_private_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def _publish_transfer_artifacts(
    staging: Path, output_dir: Path, report: dict[str, Any]
) -> None:
    report_name = Path(report["report_path"]).name
    unique_names = [Path(item["path"]).name for item in report["archives"].values()]
    unique_names.append(report_name)
    for name in unique_names:
        if (output_dir / name).exists():
            raise FileExistsError(f"transfer artifact already exists: {name}")
    report["output_directory"] = str(output_dir)
    for item in [
        *report["archives"].values(),
        report["installer"],
        *report.get("operator_tools", {}).values(),
        *report["runbooks"].values(),
    ]:
        item["path"] = str(output_dir / Path(item["path"]).name)
    report["report_path"] = str(output_dir / report_name)
    _write_private_json(
        staging / report_name,
        {key: value for key, value in report.items() if key != "report_path"},
    )
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    # Copy before touching the previous release. Incoming files and backups
    # must share the destination filesystem so replacement also works when
    # system temporary storage is on a different device.
    publication = Path(tempfile.mkdtemp(prefix=".publishing-", dir=output_dir))
    incoming = publication / "incoming"
    backups = publication / "backups"
    incoming.mkdir()
    backups.mkdir()
    touched: list[str] = []
    cleanup = True
    try:
        for path in staging.iterdir():
            shutil.copy2(path, incoming / path.name)
        archive_names = unique_names[:-1]
        sidecar_names = sorted(
            path.name for path in staging.iterdir() if path.name not in unique_names
        )
        # Archives precede the installer; the report is the completion marker.
        for name in [*archive_names, *sidecar_names, report_name]:
            target = output_dir / name
            if target.exists() or target.is_symlink():
                shutil.copy2(target, backups / name, follow_symlinks=False)
            touched.append(name)
            os.replace(incoming / name, target)
    except BaseException:
        # Keep backups even if recovery itself is interrupted.
        cleanup = False
        try:
            for name in reversed(touched):
                target = output_dir / name
                target.unlink(missing_ok=True)
                backup = backups / name
                if backup.exists() or backup.is_symlink():
                    os.replace(backup, target)
        except OSError as exc:
            raise OSError(
                f"publication rollback failed; previous files retained at {backups}"
            ) from exc
        cleanup = True
        raise
    finally:
        if cleanup:
            shutil.rmtree(publication)


def _write_transfer_runbooks(
    *,
    output_dir: Path,
    timestamp: str,
    git_info: dict[str, Any],
    archives: dict[str, Any],
    report_name: str,
    installer: dict[str, Any],
) -> dict[str, Any]:
    runbooks = {}
    for mode, name in (
        ("upgrade", TRANSFER_RUNBOOK_NAME),
        ("reinitialize", TRANSFER_REINITIALIZE_RUNBOOK_NAME),
    ):
        path = output_dir / name
        _write_private_text(
            path,
            _transfer_runbook(
                timestamp=timestamp,
                git_info=git_info,
                archives=archives,
                report_name=report_name,
                installer=installer,
                initialize_soc_dev=mode == "reinitialize",
            ),
        )
        runbooks[mode] = _sidecar_result(path)
    return runbooks


def inspect_archive(path: Path) -> dict[str, Any]:
    """Verify archive path safety, manifest presence, and per-file digests."""

    archive = path.expanduser().resolve()
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        _assert_safe_members(members)
        manifest_members = [
            member
            for member in members
            if member.name.startswith(f"{ARCHIVE_ROOT}/TRANSFER-MANIFEST.")
            and member.name.endswith(".json")
        ]
        if len(manifest_members) != 1:
            raise ValueError("archive must contain exactly one transfer manifest")
        manifest_member = manifest_members[0]
        manifest_file = handle.extractfile(manifest_member)
        if manifest_file is None:
            raise ValueError("archive manifest cannot be read")
        manifest = json.loads(manifest_file.read().decode("utf-8"))
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("archive manifest schema is unsupported")
        if manifest.get("archive_root") != ARCHIVE_ROOT:
            raise ValueError("archive manifest root is invalid")
        archive_kind = manifest.get("archive_kind")
        if archive_kind not in {"source", "private_overlay"}:
            raise ValueError("archive kind is invalid")
        file_items = manifest.get("files")
        if not isinstance(file_items, list):
            raise ValueError("archive manifest files must be a list")
        expected: dict[str, dict[str, Any]] = {}
        for item in file_items:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("archive manifest contains an invalid file entry")
            relative = item["path"]
            if relative in expected:
                raise ValueError(
                    f"archive manifest contains a duplicate path: {relative}"
                )
            if archive_kind == "source":
                _assert_source_path_safe(Path(relative))
            expected[relative] = item
        if manifest.get("file_count") != len(expected):
            raise ValueError("archive manifest file count is invalid")
        metadata_names = {
            manifest_member.name,
            f"{ARCHIVE_ROOT}/INTERNAL-BUNDLE-README.{archive_kind}.md",
        }
        expected_names = metadata_names | {
            f"{ARCHIVE_ROOT}/{relative}" for relative in expected
        }
        actual_names = {member.name for member in members}
        if actual_names != expected_names or len(actual_names) != len(members):
            raise ValueError("archive member inventory does not match its manifest")
        verified = 0
        for relative, item in expected.items():
            member = handle.getmember(f"{ARCHIVE_ROOT}/{relative}")
            kind = item.get("kind")
            if kind == "symlink":
                if not member.issym() or member.linkname != item.get("target"):
                    raise ValueError(f"archive symlink mismatch: {relative}")
                verified += 1
                continue
            if kind != "file" or not member.isfile():
                raise ValueError(f"archive member kind mismatch: {relative}")
            extracted = handle.extractfile(member)
            if extracted is None:
                raise ValueError(f"archive file cannot be read: {relative}")
            data = extracted.read()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"archive digest mismatch: {relative}")
            verified += 1
    return {
        "archive": str(archive),
        "archive_sha256": _sha256_file(archive),
        "archive_size_bytes": archive.stat().st_size,
        "kind": manifest.get("archive_kind"),
        "file_count": len(expected),
        "verified_file_count": verified,
        "safe_member_paths": True,
        "manifest_valid": True,
    }


def _write_archive(
    target: Path,
    *,
    root: Path,
    paths: Sequence[Path],
    manifest: MappingLike,
    readme: str,
) -> None:
    temporary = target.with_name(f".{target.name}.tmp")
    archive_kind = str(manifest["archive_kind"])
    try:
        with tarfile.open(temporary, "w:gz", compresslevel=9) as archive:
            _add_bytes(
                archive,
                f"{ARCHIVE_ROOT}/INTERNAL-BUNDLE-README.{archive_kind}.md",
                readme.encode("utf-8"),
                mode=0o600,
            )
            _add_bytes(
                archive,
                f"{ARCHIVE_ROOT}/TRANSFER-MANIFEST.{archive_kind}.json",
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                ),
                mode=0o600,
            )
            for relative in paths:
                source = root / relative
                arcname = f"{ARCHIVE_ROOT}/{relative.as_posix()}"
                info = archive.gettarinfo(str(source), arcname=arcname)
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                if manifest["private"] and info.isfile():
                    info.mode = 0o600
                if info.isfile():
                    with source.open("rb") as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


class MappingLike(dict[str, Any]):
    """Type marker for JSON archive manifests."""


def _archive_manifest(
    *,
    archive_kind: str,
    root: Path,
    paths: Sequence[Path],
    created_at: str,
    git_info: dict[str, Any],
    private: bool,
) -> MappingLike:
    files = []
    for relative in paths:
        source = root / relative
        if source.is_symlink():
            files.append(
                {
                    "path": relative.as_posix(),
                    "kind": "symlink",
                    "target": os.readlink(source),
                }
            )
            continue
        files.append(
            {
                "path": relative.as_posix(),
                "kind": "file",
                "size_bytes": source.stat().st_size,
                "sha256": _sha256_file(source),
                "mode": oct(0o600 if private else stat.S_IMODE(source.stat().st_mode)),
            }
        )
    return MappingLike(
        schema_version=MANIFEST_SCHEMA_VERSION,
        archive_kind=archive_kind,
        created_at=created_at,
        archive_root=ARCHIVE_ROOT,
        private=private,
        git=git_info,
        file_count=len(files),
        files=files,
        source_excluded_prefixes=list(SOURCE_EXCLUDED_PREFIXES) if not private else [],
    )


def _archive_result(path: Path, manifest: MappingLike) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "file_count": manifest["file_count"],
        "private": manifest["private"],
    }


def _sidecar_result(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _source_path_excluded(relative: str) -> bool:
    return any(relative.startswith(prefix) for prefix in SOURCE_EXCLUDED_PREFIXES)


def _assert_source_path_safe(relative: Path) -> None:
    if relative.name in SOURCE_FORBIDDEN_NAMES:
        raise ValueError(
            f"forbidden local configuration entered source archive: {relative}"
        )
    if relative.suffix.lower() in SOURCE_FORBIDDEN_SUFFIXES:
        raise ValueError(f"forbidden private data entered source archive: {relative}")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe source archive path: {relative}")


def _assert_required_handoff_sources(paths: Sequence[Path]) -> None:
    available = {path.as_posix() for path in paths}
    missing = sorted(set(REQUIRED_HANDOFF_SOURCE_PATHS) - available)
    if missing:
        raise ValueError(
            f"required internal handoff source files are missing: {missing}"
        )


def _assert_safe_members(members: Iterable[tarfile.TarInfo]) -> None:
    prefix = PurePosixPath(ARCHIVE_ROOT)
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] != prefix.parts:
            raise ValueError(f"unsafe archive member: {member.name}")
        if member.isdev() or member.isfifo():
            raise ValueError(f"unsupported archive member: {member.name}")
        if member.islnk():
            raise ValueError(
                f"hard links are unsupported in transfer archives: {member.name}"
            )
        if member.issym():
            target = PurePosixPath(member.linkname)
            if target.is_absolute() or ".." in target.parts:
                raise ValueError(f"unsafe archive symlink target: {member.name}")


def _add_bytes(
    archive: tarfile.TarFile,
    name: str,
    data: bytes,
    *,
    mode: int,
) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = 0
    archive.addfile(info, io.BytesIO(data))


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    _write_private_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _write_private_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _git_info(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "worktree_dirty": bool(run("status", "--porcelain")),
    }


def _assert_source_freeze_allowed(
    git_info: dict[str, Any],
    *,
    allow_dirty: bool,
) -> None:
    worktree_dirty = git_info.get("worktree_dirty")
    if not isinstance(worktree_dirty, bool):
        raise ValueError("Git worktree state is unavailable; refusing transfer build")
    if worktree_dirty and not allow_dirty:
        raise ValueError(
            "Git worktree is dirty; commit the intended source first or use "
            "--allow-dirty for a development-only archive"
        )


def _assert_private_overlay_config_ready(root: Path) -> None:
    """Reject stale or placeholder local profiles before packaging secrets."""

    env_path = root / ".env.soc-dev.local"
    config_path = root / "config.pingan-dev.local"
    for path in (env_path, config_path):
        if path.stat().st_mode & 0o077:
            raise ValueError(f"private overlay config must be mode 0600: {path.name}")
        if "/Users/" in path.read_text(encoding="utf-8"):
            raise ValueError(
                f"private overlay config contains a developer-specific path: {path.name}"
            )

    expected_config_version = _read_config_version(root / "config.example.yaml")
    private_config_version = _read_config_version(config_path)
    if private_config_version != expected_config_version:
        raise ValueError(
            "private config_version does not match config.example.yaml: "
            f"expected {expected_config_version}, found {private_config_version}"
        )
    _assert_private_config_contract(config_path.read_text(encoding="utf-8"))

    key_path = root / ".secrets/eagw-private-key.der"
    if not key_path.is_file() or not key_path.stat().st_size:
        raise ValueError("private overlay is missing the prepared EAGW RSA key")
    if key_path.stat().st_mode & 0o077:
        raise ValueError("private overlay EAGW RSA key must be mode 0600")

    values = _shell_export_values(env_path.read_text(encoding="utf-8"))
    obsolete = sorted(PRIVATE_ENV_OBSOLETE_KEYS & values.keys())
    if obsolete:
        raise ValueError(
            "private environment contains obsolete keys: " + ", ".join(obsolete)
        )
    missing = sorted(PRIVATE_ENV_REQUIRED_KEYS - values.keys())
    if missing:
        raise ValueError(
            "private environment is missing required keys: " + ", ".join(missing)
        )
    unresolved = sorted(
        name
        for name in PRIVATE_ENV_REQUIRED_KEYS
        if _is_unresolved_private_value(values[name])
    )
    if unresolved:
        raise ValueError(
            "private environment contains unresolved values: " + ", ".join(unresolved)
        )
    configured_stg_workflow_keys = {
        name
        for name in PRIVATE_ENV_OPTIONAL_AGENT_PLATFORM_STG_KEYS
        if values.get(name, "").strip()
    }
    if configured_stg_workflow_keys and configured_stg_workflow_keys != (
        PRIVATE_ENV_OPTIONAL_AGENT_PLATFORM_STG_KEYS
    ):
        missing_stg_workflow_keys = sorted(
            PRIVATE_ENV_OPTIONAL_AGENT_PLATFORM_STG_KEYS - configured_stg_workflow_keys
        )
        raise ValueError(
            "private environment contains a partial Agent Platform STG profile: "
            + ", ".join(missing_stg_workflow_keys)
        )
    unresolved_stg_workflow_keys = sorted(
        name
        for name in configured_stg_workflow_keys
        if _is_unresolved_private_value(values[name])
    )
    if unresolved_stg_workflow_keys:
        raise ValueError(
            "private environment contains unresolved Agent Platform STG values: "
            + ", ".join(unresolved_stg_workflow_keys)
        )
    if values["SOC_PINGAN_MODEL_GATEWAY_RSA_PRIVATE_KEY_FILE"] != (
        "${SOC_REPO_ROOT}/.secrets/eagw-private-key.der"
    ):
        raise ValueError(
            "private environment must use the portable prepared EAGW RSA key path"
        )
    expected_values = {
        "PINGAN_MODEL_GATEWAY_BASE_URL": "http://127.0.0.1:4001/v1",
        "PINGAN_MODEL_GATEWAY_MODEL": "deepseek-v4-flash",
        "SOC_PINGAN_MODEL_GATEWAY_ENABLED": "true",
        "SOC_PINGAN_MODEL_GATEWAY_HOST": "127.0.0.1",
        "SOC_PINGAN_MODEL_GATEWAY_PORT": "4001",
        "SOC_PINGAN_MODEL_GATEWAY_MODEL_ALIAS": "deepseek-v4-flash",
        "SOC_PINGAN_MODEL_GATEWAY_PROVIDER": "eagw",
        "SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY": "16",
        "SOC_ANALYZER_MODE": "llm",
        "SOC_LLM_MODEL": "deepseek-v4-flash",
        "SOC_LLM_MAX_CONCURRENCY": "16",
        "SOC_PINGAN_COMPAT_ENABLED": "true",
        "SOC_PINGAN_COMPAT_HOST": "0.0.0.0",
        "SOC_PINGAN_COMPAT_PORT": "8090",
        "SOC_PINGAN_LEGACY_LIFECYCLE_MODE": "fake",
        "SOC_PINGAN_LEGACY_CALLBACK_MODE": "fake",
        "SOC_PINGAN_LEGACY_WORKER_AUTO_MIGRATE": "false",
        "SOC_PINGAN_ENV": "dev",
        "SOC_PINGAN_ZEUS_PRD_BASE_URL": "https://isec-gw.paic.com.cn",
        "SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS": "isec-gw.paic.com.cn",
        "SOC_PINGAN_ZEUS_PRD_APP_ID": "SEC-MODEL",
        "SOC_PINGAN_ZEUS_STG_BASE_URL": "https://isec-gw-stg.paic.com.cn",
        "SOC_PINGAN_ZEUS_STG_ALLOWED_HOSTS": "isec-gw-stg.paic.com.cn",
        "SOC_PINGAN_ZEUS_STG_APP_ID": "SEC-MODEL",
        "SOC_PINGAN_ZEUS_ENV": "prd",
        "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw.paic.com.cn",
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw.paic.com.cn",
        "SOC_PINGAN_ZEUS_APP_ID": "SEC-MODEL",
        "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": "CALL_PINGAN_ZEUS_PRD",
        "SOC_PINGAN_WORKFLOW_ENV": "prd",
        "SOC_PINGAN_WORKFLOW_BASE_URL": "https://agents-api-sze.paic.com.cn",
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": "agents-api-sze.paic.com.cn",
        "SOC_PINGAN_WORKFLOW_APP_ID": "YHSYS",
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID": "1087710",
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID": "1087787",
        "SOC_PINGAN_WORKFLOW_USER_ID": "1092332",
        "SOC_PINGAN_WORKFLOW_PRD_BASE_URL": "https://agents-api-sze.paic.com.cn",
        "SOC_PINGAN_WORKFLOW_PRD_ALLOWED_HOSTS": "agents-api-sze.paic.com.cn",
        "SOC_PINGAN_WORKFLOW_PRD_APP_ID": "YHSYS",
        "SOC_PINGAN_WORKFLOW_PRD_TERMINAL_ID": "1087710",
        "SOC_PINGAN_WORKFLOW_PRD_DATACENTER_ID": "1087787",
        "SOC_PINGAN_WORKFLOW_PRD_USER_ID": "1092332",
        "SOC_PINGAN_WORKFLOW_STG_BASE_URL": "https://agents-api-stg-new.paic.com.cn",
        "SOC_PINGAN_WORKFLOW_STG_ALLOWED_HOSTS": "agents-api-stg-new.paic.com.cn",
    }
    mismatched = sorted(
        name for name, expected in expected_values.items() if values[name] != expected
    )
    if mismatched:
        raise ValueError(
            "private environment is not in the safe transfer profile: "
            + ", ".join(mismatched)
        )
    active_target_pairs = (
        ("SOC_PINGAN_ZEUS_BASE_URL", "SOC_PINGAN_ZEUS_PRD_BASE_URL"),
        ("SOC_PINGAN_ZEUS_ALLOWED_HOSTS", "SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS"),
        ("SOC_PINGAN_ZEUS_APP_ID", "SOC_PINGAN_ZEUS_PRD_APP_ID"),
        ("SOC_PINGAN_ZEUS_APP_KEY", "SOC_PINGAN_ZEUS_PRD_APP_KEY"),
    )
    drifted_active_values = [
        active
        for active, stored in active_target_pairs
        if values[active] != values[stored]
    ]
    if drifted_active_values:
        raise ValueError(
            "private environment active ZEUS target does not match the project "
            "DEV -> ZEUS PRD profile: " + ", ".join(drifted_active_values)
        )
    active_workflow_pairs = (
        ("SOC_PINGAN_WORKFLOW_BASE_URL", "SOC_PINGAN_WORKFLOW_PRD_BASE_URL"),
        (
            "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS",
            "SOC_PINGAN_WORKFLOW_PRD_ALLOWED_HOSTS",
        ),
        ("SOC_PINGAN_WORKFLOW_APP_ID", "SOC_PINGAN_WORKFLOW_PRD_APP_ID"),
        (
            "SOC_PINGAN_WORKFLOW_APP_SECRET",
            "SOC_PINGAN_WORKFLOW_PRD_APP_SECRET",
        ),
        (
            "SOC_PINGAN_WORKFLOW_TERMINAL_ID",
            "SOC_PINGAN_WORKFLOW_PRD_TERMINAL_ID",
        ),
        (
            "SOC_PINGAN_WORKFLOW_DATACENTER_ID",
            "SOC_PINGAN_WORKFLOW_PRD_DATACENTER_ID",
        ),
        ("SOC_PINGAN_WORKFLOW_USER_ID", "SOC_PINGAN_WORKFLOW_PRD_USER_ID"),
    )
    drifted_active_workflow_values = [
        active
        for active, stored in active_workflow_pairs
        if values[active] != values[stored]
    ]
    if drifted_active_workflow_values:
        raise ValueError(
            "private environment active Agent Platform target does not match the "
            "project DEV -> Agent Platform PRD profile: "
            + ", ".join(drifted_active_workflow_values)
        )
    service_api_keys = {
        value.strip()
        for value in values["SOC_PINGAN_MODEL_GATEWAY_API_KEYS"].split(",")
        if value.strip()
    }
    if values["PINGAN_MODEL_GATEWAY_API_KEY"] not in service_api_keys:
        raise ValueError(
            "private model-gateway client key is not accepted by the loopback service"
        )
    try:
        compat_app_keys = json.loads(values["SOC_PINGAN_COMPAT_APP_KEYS_JSON"])
    except json.JSONDecodeError as exc:
        raise ValueError(
            "private compatibility app-key mapping is invalid JSON"
        ) from exc
    if (
        not isinstance(compat_app_keys, dict)
        or not compat_app_keys
        or not all(
            isinstance(name, str)
            and name.strip()
            and isinstance(value, str)
            and value.strip()
            for name, value in compat_app_keys.items()
        )
    ):
        raise ValueError(
            "private compatibility app-key mapping requires non-empty string pairs"
        )


def _read_config_version(path: Path) -> int:
    if not path.is_file():
        raise ValueError(f"config_version source is missing: {path.name}")
    match = _CONFIG_VERSION.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"config_version is missing or invalid: {path.name}")
    return int(match.group("version"))


def _assert_private_config_contract(content: str) -> None:
    if "PINGAN_LITELLM_" in content:
        raise ValueError("private config contains obsolete LiteLLM model references")
    required_model_lines = (
        "model: deepseek-v4-flash",
        "api_base: $PINGAN_MODEL_GATEWAY_BASE_URL",
        "api_key: $PINGAN_MODEL_GATEWAY_API_KEY",
        "disable_streaming: true",
    )
    if any(line not in content for line in required_model_lines):
        raise ValueError(
            "private config is missing the project model-gateway references "
            "or disable_streaming: true"
        )
    database_block = _top_level_yaml_block(content, "database")
    if (
        re.search(r"(?m)^\s+backend:\s*sqlite\s*(?:#.*)?$", database_block) is None
        or re.search(
            r"(?m)^\s+sqlite_dir:\s*\.deer-flow/data\s*(?:#.*)?$",
            database_block,
        )
        is None
    ):
        raise ValueError(
            "private config must use database.backend=sqlite and "
            "sqlite_dir=.deer-flow/data"
        )


def _top_level_yaml_block(content: str, key: str) -> str:
    lines = content.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == f"{key}:" and not line[:1].isspace():
            start = index + 1
            break
    if start is None:
        return ""
    selected: list[str] = []
    for line in lines[start:]:
        if (
            line.strip()
            and not line[:1].isspace()
            and not line.lstrip().startswith("#")
        ):
            break
        selected.append(line)
    return "\n".join(selected)


def _shell_export_values(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("export "):
            continue
        assignment = line.removeprefix("export ").strip()
        name, separator, raw_value = assignment.partition("=")
        name = name.strip()
        if not separator or not _SHELL_NAME.fullmatch(name):
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name] = value.strip()
    return values


def _is_unresolved_private_value(value: str) -> bool:
    normalized = value.strip()
    return (
        not normalized
        or (normalized.startswith("<") and normalized.endswith(">"))
        or normalized.lower() in {"changeme", "todo", "replace-me"}
    )


def _transfer_installer(*, archives: dict[str, Any]) -> str:
    source = archives["source"]
    private = archives.get("private_overlay")
    source_name = Path(str(source["path"])).name
    source_sha = str(source["sha256"])
    if private is None:
        private_name = "<private-overlay-not-built>"
        private_sha = "<unavailable>"
        private_ready = "false"
    else:
        private_name = Path(str(private["path"])).name
        private_sha = str(private["sha256"])
        private_ready = "true"
    persistent_path_lines = "\n".join(
        f'  "{path}"' for path in HOST_DEV_PERSISTENT_PATHS
    )
    return f"""#!/usr/bin/env bash
if [[ "${{BASH_SOURCE[0]}}" != "$0" ]]; then
  printf '%s\n' "ERROR: do not source this installer; run: bash ${{BASH_SOURCE[0]}}" >&2
  return 2
fi

set -euo pipefail

fail() {{
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}}

require_command() {{
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}}

verify_archive() {{
  local archive="$1"
  local expected="$2"
  local digest_line
  local actual
  [[ -f "$archive" ]] || fail "transfer artifact not found: $archive"
  digest_line="$(shasum -a 256 "$archive")" || fail "cannot hash: $archive"
  actual="${{digest_line%% *}}"
  [[ "$actual" == "$expected" ]] || fail "SHA-256 mismatch: $archive"
}}

rollback_checkout() {{
  rm -rf -- "$TARGET_REPO"
  if [[ -n "$BACKUP_REPO" && -d "$BACKUP_REPO" ]]; then
    mv -- "$BACKUP_REPO" "$TARGET_REPO" || return 1
  fi
}}

restore_persistent_state() {{
  local relative_path
  local source_path
  local target_path
  local preserved_count=0
  [[ -n "$BACKUP_REPO" ]] || return 0

  local persistent_paths=(
{persistent_path_lines}
  )
  for relative_path in "${{persistent_paths[@]}}"; do
    source_path="$BACKUP_REPO/$relative_path"
    target_path="$TARGET_REPO/$relative_path"
    if [[ ! -e "$source_path" && ! -L "$source_path" ]]; then
      continue
    fi
    if [[ -e "$target_path" || -L "$target_path" ]]; then
      printf 'ERROR: new package unexpectedly owns persistent path: %s\n' \
        "$relative_path" >&2
      return 1
    fi
    mkdir -p -- "$(dirname -- "$target_path")" || return 1
    cp -Rp -- "$source_path" "$target_path" || return 1
    preserved_count=$((preserved_count + 1))
    printf '  preserved: %s\n' "$relative_path"
  done
  printf 'Preserved %s existing runtime-state path(s).\n' "$preserved_count"
}}

SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd -P)"
TRANSFER_DIR="$SCRIPT_DIR"
TARGET_REPO="$HOME/deer-flow"
SOURCE_ARCHIVE="$TRANSFER_DIR/{source_name}"
PRIVATE_ARCHIVE="$TRANSFER_DIR/{private_name}"
SOURCE_SHA256="{source_sha}"
PRIVATE_SHA256="{private_sha}"
PRIVATE_OVERLAY_READY="{private_ready}"

[[ "$PRIVATE_OVERLAY_READY" == "true" ]] || \
  fail "this is a source-only package; build and transfer the private overlay first"

for command_name in tar shasum lsof python3.12 mktemp mv cp chmod mkdir dirname; do
  require_command "$command_name"
done

echo "[1/5] Verifying transfer archives..."
verify_archive "$SOURCE_ARCHIVE" "$SOURCE_SHA256"
verify_archive "$PRIVATE_ARCHIVE" "$PRIVATE_SHA256"

STAGE_DIR="$(mktemp -d "$TRANSFER_DIR/.install-stage.XXXXXX")"
cleanup_stage() {{
  rm -rf -- "$STAGE_DIR"
}}
trap cleanup_stage EXIT

echo "[2/5] Extracting and validating the new checkout..."
tar -xzf "$SOURCE_ARCHIVE" -C "$STAGE_DIR"
tar -xzf "$PRIVATE_ARCHIVE" -C "$STAGE_DIR"
STAGED_REPO="$STAGE_DIR/{ARCHIVE_ROOT}"
for required_path in \
  "scripts/soc_pingan_macos_host_dev.py" \
  ".env.soc-dev.local" \
  "config.pingan-dev.local" \
  ".secrets/eagw-private-key.der"; do
  [[ -f "$STAGED_REPO/$required_path" ]] || \
    fail "staged checkout is incomplete: $required_path"
done

echo "[3/5] Stopping the old Host DEV checkout, if present..."
if [[ -e "$TARGET_REPO" && ! -d "$TARGET_REPO" ]]; then
  fail "target exists but is not a directory: $TARGET_REPO"
fi
if [[ -d "$TARGET_REPO" ]]; then
  [[ -f "$TARGET_REPO/scripts/soc_pingan_macos_host_dev.py" ]] || \
    fail "existing target is not a recognized Host DEV checkout: $TARGET_REPO"
  if ! (
    cd "$TARGET_REPO"
    python3.12 scripts/soc_pingan_macos_host_dev.py stop
  ); then
    fail "old Host DEV stop failed; the existing checkout was not replaced"
  fi
fi

busy=0
for port in 3000 8001 2026 4001 8090; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    printf 'ERROR: TCP %s is still in use:\n' "$port" >&2
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
    busy=1
  fi
done
[[ "$busy" -eq 0 ]] || \
  fail "stop the listed process(es), then run this installer again; the old checkout was not replaced"

echo "[4/5] Replacing the checkout..."
BACKUP_REPO=""
if [[ -d "$TARGET_REPO" ]]; then
  BACKUP_REPO="$HOME/.deer-flow-install-backup-$$"
  [[ ! -e "$BACKUP_REPO" ]] || fail "backup path already exists: $BACKUP_REPO"
  mv -- "$TARGET_REPO" "$BACKUP_REPO"
fi
if ! mv -- "$STAGED_REPO" "$TARGET_REPO"; then
  if [[ -n "$BACKUP_REPO" && -d "$BACKUP_REPO" ]]; then
    mv -- "$BACKUP_REPO" "$TARGET_REPO" || true
  fi
  fail "could not install the staged checkout; the previous checkout was restored when possible"
fi

if ! restore_persistent_state; then
  rollback_checkout || true
  fail "could not preserve runtime state; the previous checkout was restored when possible"
fi

if ! (
  chmod 700 "$TARGET_REPO/.secrets"
  chmod 600 \
    "$TARGET_REPO/.env.soc-dev.local" \
    "$TARGET_REPO/config.pingan-dev.local" \
    "$TARGET_REPO/.secrets/eagw-private-key.der"
); then
  rollback_checkout || true
  fail "could not apply private-file permissions; the previous checkout was restored when possible"
fi
if [[ -n "$BACKUP_REPO" ]]; then
  rm -rf -- "$BACKUP_REPO"
fi

echo "[5/5] Install complete: $TARGET_REPO"
echo "Next: cd \"$TARGET_REPO\" && python3.12 scripts/soc_pingan_stage_internal_corpus.py"
"""


def _transfer_runbook(
    *,
    timestamp: str,
    git_info: dict[str, Any],
    archives: dict[str, Any],
    report_name: str,
    installer: dict[str, Any],
    initialize_soc_dev: bool = False,
) -> str:
    source = archives["source"]
    private = archives.get("private_overlay")
    source_name = Path(str(source["path"])).name
    source_sha = str(source["sha256"])
    installer_name = Path(str(installer["path"])).name
    installer_sha = str(installer["sha256"])
    if private is None:
        private_name = "<private-overlay-not-built>"
        private_sha = "<unavailable>"
        private_notice = (
            "This build does not contain the private overlay. It can be inspected as a "
            "source-only development snapshot, but it is not a complete PingAn DEV handoff."
        )
    else:
        private_name = Path(str(private["path"])).name
        private_sha = str(private["sha256"])
        private_notice = (
            "The private overlay contains local configuration, credentials, the prepared "
            "EAGW RSA key, corpus manifests/index, and the reviewed EDR path catalog. The "
            "three PKL files and Workbench payload SQLite are supplied separately and are "
            "verified before use. Keep every artifact inside the approved environment."
        )
    if initialize_soc_dev:
        title = "内网 Mac DEV：完全初始化重新部署"
        mode = "reinitialize"
        introduction = (
            "仅在明确决定从零重新验证时使用本手册。先停止并卸下旧项目服务，选择是否备份，再由安装器替换旧项目代码，\n"
            "再显式重置 SOC DEV：告警结果、第一批/第二批任务、经验、草稿和审核记录从空库开始。\n"
            "账号库、STG 数据库和仓库外原始语料保留；Mac 的 Python、Node、nginx 等基础工具无需卸载。\n"
            "已明确要求从零验证时，即使旧部署已有第一批或审核成果，也按本手册重新积累。\n"
            f"**如要保留这些成果，请改用[保留数据升级手册]({TRANSFER_RUNBOOK_NAME})。**"
        )
        compatibility_followup = (
            "内网无需重复运行测试；本次从空库验证仍需重新运行第一批并审核本轮新经验，"
            "再用这些新经验验证第二批。"
        )
        install_sequence = (
            "仅在明确决定从零重新验证时：先按本节停服、选择是否备份并替换代码，第 4–6 节完成语料、依赖和预检，\n"
            "再执行第 6.1 节显式重置 SOC DEV，最后第 7 节启动。"
        )
        reset_section = """### 6.1 Restart Validation / 本次从零验证：重置 SOC DEV

仅在明确决定从零重新验证时，在第一次启动前执行本节。
普通保留数据升级跳过本节。重置只处理 `soc_agent_dev.db` 和它的 SQLite sidecars：
SOC DEV 的研判、批次任务、经验、候选与审核记录从空库重新开始；账号、STG、原始语料、配置和私钥保持不变。
数据库备份与第3.1节的项目压缩备份独立选择。下方 `soc_backup_database=yes` 保留默认备份；
改成 `no` 会直接删除本次旧 DEV 数据库及附属文件，不压缩、不复制、不读取整库计算哈希，不能通过本次重置恢复。
如果第3.1节已备份整个项目，那份压缩包已包含数据库，可选择不再单独备份。

先快速预览（不计算整库哈希，也不删除文件），确认 `database` 是本 checkout 的 `soc_agent_dev.db`、`ready=true`、`blockers=[]`：

```bash
bash <<'BASH'
set -euo pipefail
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py stop
backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py reset-dev-data --no-backup
BASH
```

确认范围正确后，修改下面第一行选项再执行。只在本次从零验证开始前执行一次：

```bash
bash <<'BASH'
set -euo pipefail
soc_backup_database=yes # yes：备份数据库；no：不备份，直接清空 DEV 数据库
umask 077
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py stop
case "$soc_backup_database" in
  yes|no) ;;
  *) printf 'soc_backup_database must be yes or no\\n' >&2; exit 1 ;;
esac
mkdir -p backend/.deer-flow/internal-host-dev
receipt=backend/.deer-flow/internal-host-dev/dev-reset-receipt.json
receipt_pending="$(mktemp "$receipt.XXXXXX")"
if [[ "$soc_backup_database" == no ]]; then
  backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py reset-dev-data --no-backup --confirm RESET-SOC-DEV > "$receipt_pending"
else
  backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py reset-dev-data --confirm RESET-SOC-DEV > "$receipt_pending"
fi
mv "$receipt_pending" "$receipt"
backend/.venv/bin/python -m json.tool "$receipt"
BASH
```

报告必须是 `status=reset` 或 `status=already_empty`（原本就是空库）。
不备份时显示 `backup_enabled=false`、`backup_directory=null`；备份时记录实际 `backup_directory`。
报告保存在 `backend/.deer-flow/internal-host-dev/dev-reset-receipt.json`，与批次手册的恢复入口一致。
选择备份时，归档位于 `backend/.deer-flow/data/soc-dev-reset-backups/`。第 7 节普通 `start` 自动初始化空 SOC Schema。
这就是本次所需初始化，不需要手工 migration、删库、重新安装 Mac 工具或重新搬运未变化的语料。
后续重启、继续积累或第二批验证都不要再次执行；进入批次操作手册时也跳过其中已完成的重置步骤。"""
        database_recovery = "服务尚未启动；保留已有数据库排查。查明原因后重试；需要重新开始本次 DEV 验证时，只使用第 6.1 节受限重置"
        batch_start_instruction = (
            "已完成本手册第 6.1 节重置时，直接从批次手册第 2 节网页操作开始，跳过其第 1.1 节；\n"
            "本轮从新的第一批开始，不恢复旧结果、候选或经验。仅尚未重置时才执行批次手册第 1.1 节，\n"
            "后续重启、续跑和第二批开始都不再次重置。"
        )
        batch_restart_instruction = (
            "本轮第一批及经验审核完成后，如以后只需重跑第二批，再使用“重新配置并全部重跑”。\n"
            "先暂停并等待运行中为0，确认完整设置后点击“全部重新运行”；该操作保留本轮第一批及已审核经验，\n"
            "旧的第二批结果保留在历史中，不能替代本次空库初始化。逐步操作见项目内 `docs/soc-validation-restart.md`。"
        )
        capacity_instruction = (
            "未保存本机并发设置时，本次全新 SOC DEV 默认以 `16` 开始验证，无需先调低。\n"
            "已保存的较低并发设置仍保留；若页面显示旧值，开始本次从零验证前，\n"
            "在部署 Mac 本机的运行设置中将“最大并发”保存为 `16`，确认页面上限后再开始积累。"
        )
    else:
        title = "内网 Mac DEV：保留数据升级"
        mode = "upgrade"
        introduction = (
            "适用于已有部署，尤其是第一批已完成、正在审核经验或验证第二批的 Mac。\n"
            "请先保存正在编辑的审核草稿，暂停同事操作，记下第一批完成数量、待审核及已审核经验。\n"
            "在服务仍运行时，将仍在运行的旧事实准备轮次及旧学习/验证轮次通过网页暂停或 CLI `pause` 持久化为 `paused`，\n"
            "逐轮确认 `active_count=0`；只执行 Host `stop` 不会把轮次改为暂停，后续启动可能恢复领取旧任务。\n"
            "再按本篇停服、选择是否备份、替换代码、恢复语料、安装依赖和启动。整个流程保留数据库和原实验历史。\n"
            f"仅另外决定从零验证时才使用[完全初始化重新部署手册]({TRANSFER_REINITIALIZE_RUNBOOK_NAME})。"
        )
        compatibility_followup = (
            "内网无需重复运行测试；旧实验结果与审核记录保留。已使用第一批9,364条、第二批5,917条名单的部署，\n"
            "升级后保留该实验第一批及已审核经验；只有仍使用旧第一批2,997条名单的部署，才需要独立运行新版名单。"
        )
        install_sequence = (
            "本手册保留运行数据：先按本节选择是否备份并替换代码，第 4–6 节完成语料、依赖和预检，\n"
            "核对第 6.1 节保留范围后，直接执行第 7 节启动。"
        )
        reset_section = """### 6.1 Preserve Existing Validation / 保留已有 SOC DEV 数据

本手册保留 SOC DEV，本节没有需要执行的命令。第一批研判结果、批次进度、经验样本、待审核经验及已完成的审核记录均保留。
第 3 节安装器已迁入完整数据库目录及 SQLite sidecars；普通升级不要清库。
完成第 6 节预检后，直接继续第 7 节启动。启动会升级现有数据库结构，不会重新初始化已有数据。
启动后核对原实验历史和审核记录。若当前已经使用第一批9,364条、第二批5,917条名单，
本次升级保留该实验第一批结果及已审核经验，无需重新积累或重新审核这些经验。
需要让已有第一批结果接入两批连续积累时，先按第12节预览并接续，再启动新的第二批。
只有仍使用旧第一批2,997条名单时，页面才会切换到新版名单对应的新实验；原实验记录不会改写成新名单，
旧名单结果不自动算作新版进度。
只有另外选择从空库验证时才使用重新初始化手册。

本次替换源码会改变运行配置版本。升级前已排队、尚未完成的任务仍保留原版本，点击“继续”时
可能出现 `configuration_changed`（配置已变化）。原任务与结果保留，不能通过原轮次补跑绕过配置版本检查。
第二批需要采用本次新功能或新设置时，先等待运行中为0，
点击“重新配置并全部重跑”，确认设置后点击“全部重新运行”。这会创建整批新任务，成功、失败和未运行
告警全部重跑，旧结果仍保留在历史中。完整步骤见 `docs/soc-validation-restart.md`。
上述第二批重跑保留该实验第一批和审核记录；不同名单的实验仍不能相互承接进度。仅在同一代码版本下修改“最大并发”不会触发
这个版本检查，也不需要重新建立任务。"""
        database_recovery = "服务尚未启动；保留已有数据库和备份排查，查明原因后重试，不要通过清库解决启动失败"
        batch_start_instruction = (
            "保留数据升级跳过批次手册第 1.1 节重置。当前已是第一批9,364条、第二批5,917条时，\n"
            "保留已经完成的第一批和已审核经验；若需接续已有结果，按第12节处理，再为第二批建立新轮次。\n"
            "仅第一批尚未开始，或仍从旧2,997条名单切换时，才从网页第一批“开始积累”启动。"
        )
        batch_restart_instruction = (
            "如果升级后旧任务提示 `configuration_changed`，旧轮次保持原冻结配置，不能通过补跑绕过；\n"
            "暂停并等待运行中为0，再点击第二批“重新配置并全部重跑”，确认设置后点击“全部重新运行”。\n"
            "预期新轮次覆盖第二批5,917条，第一批与审核经验保留；完整步骤见 `docs/soc-validation-restart.md`。"
        )
        capacity_instruction = (
            "保留数据升级继续使用已保存的本机并发设置。若旧库曾出现数据库锁等待，\n"
            "升级后可先在本机把“最大并发”保存为 `2`，再恢复运行，\n"
            "观察页面响应和任务结果正常后逐步调高；减少并发不能保证消除其他长事务造成的锁等待。"
        )
    operations_steps = (
        (Path(__file__).resolve().parents[1] / "docs/soc-corpus-operations-runbook.md")
        .read_text(encoding="utf-8")
        .replace(
            'cd "$HOME/deer-flow"',
            'export TARGET_REPO="$HOME/deer-flow"\ncd "$TARGET_REPO"',
        )
    )
    return f"""# {title}

> Built: `{timestamp}`
> Source commit: `{git_info["commit"]}` (`{git_info["branch"]}`)
> Target: Apple Silicon macOS, Python `3.12+`, no Docker
> Install path: `$HOME/deer-flow`
> Procedure: `{mode}`
> Following this procedure resets SOC DEV: `{str(initialize_soc_dev).lower()}`

{introduction}

**只选择一份手册，不要两份依次执行。** 两份手册共用相同源码包、私有配置包和安装器。
若正在按照此前单份 Runbook 操作，请从已完成的步骤后继续；无需因文档更新重复已经完成的备份或安装。
每一步确认成功再继续；具体包身份与校验值见第 1 节。

本手册由 `scripts/build_pingan_internal_transfer.py` 随包生成。文件名、commit 和
SHA-256 与本次交付一致，不需要额外 nginx/LAN hotfix。
外网冻结前已依次运行 `soc_pingan_prepare_legacy_model_gateway_profile.py --apply` 和
`soc_pingan_prepare_legacy_workflow_profile.py --apply`。旧源码不进入 source archive；解析出的
凭证和 RSA key 只进入受保护 private overlay，内网不需要再依赖旧项目。
本机进程、SQLite、Workbench 和 Tenant Policy 仍属于 DEV；所有共享 ZEUS Provider 明确指向
PRD `isec-gw.paic.com.cn`，并受独立 target、host allowlist 和 production confirmation 三重门禁。

## 1. Package Identity / 包身份

`READY-TO-TRANSFER` 应只保留本次交付的以下文件：

```text
{source_name}
{private_name}
{report_name}
{TRANSFER_RUNBOOK_NAME}
{TRANSFER_REINITIALIZE_RUNBOOK_NAME}
{installer_name}
soc_stage_report_summary.py
```

SHA-256：

```text
{source_sha}  {source_name}
{private_sha}  {private_name}
{installer_sha}  {installer_name}
```

{private_notice}

## 2. Verify / 传输后校验

```bash
export TRANSFER_DIR="$HOME/READY-TO-TRANSFER"
cd "$TRANSFER_DIR"

shasum -a 256 \\
  "{source_name}" \\
  "{private_name}" \\
  "{installer_name}"
```

输出必须与第 1 节完全一致。随后检查 report：

```bash
cd "$HOME/READY-TO-TRANSFER"
cat "{report_name}"
```

必须看到 `source_worktree_dirty=false`、`final_handoff_eligible=true`、
`required_source_inventory_complete=true` 和 `compatibility_check.status=passed`。
`compatibility_check` 记录旧记录展示、已审核经验复用与越界拒绝的离线检查结果，
同时绑定本包源码 commit、文件指纹和源码归档 SHA-256。打包程序已自动完成这些检查，
{compatibility_followup}
`default_runbook` 标明本次推荐入口；`reset_soc_dev_requested` 是交付选择，
两份说明文件同时存在不代表应当重置。`runbooks` 分别记录两份文档的 SHA-256。

## 3. Install Or Data-Preserving Redeploy / 安装或保留数据升级

{install_sequence}安装器本身始终保留运行数据；后续数据处理以本手册第 6.1 节为准。
不要把“重新初始化演练”理解为删除整个仓库或账号。

### 3.1 Before Replacement / 替换前备份（可选）

已有成功部署的 Mac 先执行下面一块；全新机器没有旧 checkout 时跳过本小节。
停掉旧 Host、确认端口释放，再交给第 3.2 节安装器替换，就是卸下旧项目服务的过程；
不要先手动删除 `$HOME/deer-flow`，也不要卸载系统级 Python、Node 或 nginx。
先确认四个原始语料文件在仓库外的 `$HOME/Downloads/source`、`$HOME/Downloads/corpus` 有副本。
若只有仓库内副本，先复制到第 4 节所列路径并完成校验，再继续；不要先运行安装器。
数据 hash 未变时不需要重新跨网传输。

先修改下面的 `soc_backup_project`，再执行整块命令：

- `yes`：压缩备份完整旧项目（含数据库）到 `$HOME/deer-flow-backups/`，默认选择。
- `no`：跳过项目压缩和哈希计算，不创建上述备份；安装器仍保留当前数据库和运行数据。

两种选择都会检查语料、停服和检查五个端口。2026端口暂未释放时，等待释放再重试本节。
选择备份时，压缩和哈希计算可能耗时较久；直到出现 `Verified stopped-checkout backup:` 才完成。
选择不备份时，会显示 `Project backup skipped by operator`。随后继续第3.2节。

```bash
bash <<'BASH'
set -euo pipefail
soc_backup_project=yes # yes：备份整个项目；no：跳过压缩备份
umask 077
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
test ! -L "$TARGET_REPO"
test -f scripts/soc_pingan_macos_host_dev.py
case "$soc_backup_project" in
  yes|no) ;;
  *) printf 'soc_backup_project must be yes or no\\n' >&2; false ;;
esac
printf '[backup 1/4] Checking corpus / 检查仓库外语料\\n'
for corpus_file in \\
  "$HOME/Downloads/source/full_alert_2026_month_forth_sample_200.pkl" \\
  "$HOME/Downloads/corpus/full_alert_validation_corpus.pkl" \\
  "$HOME/Downloads/corpus/full_alert_dams_labeled_merged.pkl" \\
  "$HOME/Downloads/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite"; do
  test -f "$corpus_file"
  test -r "$corpus_file"
done
python3.12 scripts/soc_pingan_stage_internal_corpus.py
printf '[backup 2/4] Stopping services / 停止旧服务并检查端口\\n'
python3.12 scripts/soc_pingan_macos_host_dev.py stop
for port in 3000 8001 2026 4001 8090; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN; then
    printf 'Refusing backup while port %s is occupied.\\n' "$port" >&2
    false
  else
    probe_status=$?
    test "$probe_status" -eq 1
  fi
done
if [[ "$soc_backup_project" == yes ]]; then
  backup_dir="$HOME/deer-flow-backups/before-redeploy-$(date +%Y%m%dT%H%M%S)-$$"
  mkdir -p "$backup_dir"
  chmod 700 "$backup_dir"
  printf '[backup 3/4] Compressing old checkout / 正在压缩备份，请等待: %s\\n' "$backup_dir"
  tar -czf "$backup_dir/deer-flow.tar.gz" -C "$HOME" deer-flow
  printf '[backup 4/4] Computing SHA-256 / 正在计算校验值，请等待\\n'
  shasum -a 256 "$backup_dir/deer-flow.tar.gz" > "$backup_dir/SHA256SUMS"
  printf 'Verified stopped-checkout backup: %s\\n' "$backup_dir"
else
  printf 'Project backup skipped by operator / 已选择不备份项目，停服检查通过\\n'
fi
BASH
```

选择备份时，请将备份保留到新版本验收通过。安装器成功后立即删除临时回退目录，
其事务回退只覆盖安装过程；选择不备份后，本次不会留下用于后续构建或启动失败恢复的项目压缩包。
这两个选项不会清理以前的备份。恢复时也必须先停止新 Host，不得覆盖运行中的 checkout。

### 3.2 Install / 安装配套源码与私有配置

以下命令会替换当前用户的 `$HOME/deer-flow` 代码；已有部署会自动保留明确列入契约的运行数据：

```text
backend/.deer-flow/data/                 # deerflow.db、soc_agent_dev.db、soc_agent_stg.db 及 SQLite sidecars
backend/.deer-flow/.jwt_secret           # 已有登录会话签名密钥
backend/.deer-flow/users|agents|threads  # 用户 Memory、自定义 Agent 与工作区
backend/.deer-flow/integrations          # 已安装的受管 Integration Skills
backend/.deer-flow/soc-internal-validation
```

新 private overlay 中的配置、凭证与 `pingan-context` 仍以本次交付为准，**会覆盖内网旧配置**。
如果上次部署后曾在内网调整端点、凭证或 Provider 配置，安装前先与本次私有包核对并保存差异；
不要把旧 env 整体覆盖回来，以免恢复旧并发或过期配置。本次要求两个并发值都是 `16`。
旧 PID、日志、技能投影、临时缓存和旧安装包不会迁入新 checkout。

```bash
bash "$HOME/READY-TO-TRANSFER/{installer_name}"
```

安装器只读取它自身所在目录，不依赖前一节留下的 `TRANSFER_DIR`、`TARGET_REPO` 或其他
shell 状态。它在子 Bash 中依次校验准确 SHA-256、解压并检查新 checkout、停止旧 Host DEV、
确认 `3000/8001/2026/4001/8090` 全部释放，再事务式替换目录、复制允许的持久化状态并设置私有文件权限。任一步失败
都只结束安装器，不会关闭当前终端；Hash/解压/停服/端口失败时不会替换旧 checkout，替换阶段
或数据恢复失败时会尽力恢复旧目录。不要使用 `source` 或 `.` 加载安装器。

正常重部署不要删除 `deerflow.db`、`soc_agent_dev.db` 或 `soc_agent_stg.db`。
如果安装器报告
`existing target is not a recognized Host DEV checkout`，保留原目录并排查，不执行删除整个仓库的命令。

本次私有包同时包含：

```text
.secrets/eagw-private-key.der
validation/compact_zeus/data/corpus/full_alert_validation_corpus.manifest.json
validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.manifest.json
validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json
```

三个 PKL 和 Workbench payload SQLite 不在 private overlay；它们已单独保存在内网
`$HOME/Downloads/source` 与 `$HOME/Downloads/corpus`，必须按下一节校验并落位。
如果本次另有 `DATA-TO-TRANSFER`，先按其中的数据包说明更新这四个文件；只更新代码而数据hash
未变时不需要重传数据。可用 `python3.12 scripts/build_pingan_corpus_transfer.py --inspect 数据包路径`
流式校验数据包，不加载PKL、不连接业务库。它不是源码包或数据库备份。

## 4. Stage Existing Corpus / 落位内网已有语料

`$HOME` 自动使用当前 Mac 登录用户的主目录，不需要修改用户名。
先确认四个文件位于：

```text
$HOME/Downloads/source/full_alert_2026_month_forth_sample_200.pkl
$HOME/Downloads/corpus/full_alert_validation_corpus.pkl
$HOME/Downloads/corpus/full_alert_dams_labeled_merged.pkl
$HOME/Downloads/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite
```

先 dry-run，只校验文件名、大小与 SHA-256，不复制也不解析业务内容：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_stage_internal_corpus.py
```

必须看到 `ready=true`、`applied=false`，且四个文件均为 `source_verified=true`。
然后原子复制到项目规定路径并设置为 `0600`：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_stage_internal_corpus.py --apply
```

第二次必须看到 `ready=true`、`applied=true`，且四个文件均为
`target_verified=true`。校验基准来自 private overlay 中随包冻结的 corpus manifest/index；
任何文件缺失、错版本、大小或 SHA-256 不一致都会 fail closed，并且不会覆盖已有目标文件。
完成后语料 Workbench 可直接使用，不需要在内网重新生成配套索引。

## 5. Host Check And Install / 主机检查与依赖安装

前置要求：Apple Silicon macOS、Python `3.12+`、uv、Node `22+`、项目固定的
pnpm、nginx `1.23+`，以及已配置的平安 PyPI/pnpm 镜像。已有 Mac 基础工具无需重装；
完整包替换不保留旧 `backend/.venv`、`node_modules` 或前端构建，仍须执行本节项目依赖安装。

```bash
bash <<'BASH'
set -euo pipefail
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py check
python3.12 scripts/soc_pingan_macos_host_dev.py install
BASH
```

安装器使用冻结 lock、内部镜像和独立 `backend/.venv`。不要执行 `uv lock`，
也不要让 pnpm/Python 访问公网。

数据库连接会自动设置锁等待和结果保存重试，但仍可能遇到长事务竞争。已确认的内网 Python
内置 SQLite `3.45.3` 不具备上游 WAL 并发缺陷修复，本版本保留现有 `DELETE` 日志模式；
不会自动升级 SQLite，也不要手工执行切换 WAL 的 PRAGMA。只有 Python 内置 SQLite 达到
`3.51.3+`（或修复分支 `3.44.6+` / `3.50.7+`）时才自动启用 WAL。
若旧库已经使用 WAL、Python SQLite 却未修复，启动会明确停止并要求升级运行库，保留数据库文件。

不再手工初始化 SOC SQLite。Host DEV `start` 统一负责 SOC SQLite migration：它先从当前 checkout
根据 `SOC_PINGAN_ENV` 解析绝对 `soc_agent_dev.db` 或 `soc_agent_stg.db` 路径，在任何 Sidecar 和 Web 服务启动前升级 Schema，并把 Sidecar 的
重复自动迁移关闭。保留已有查询索引（`0032_corpus_revision_index`），本次升级到 `0033_pattern_revisions`，
为观察增加当前版本与替代关系，保留原观察、研判结果、经验和审核记录。
首次升级大数据库时创建索引可能耗时较长，请等待 `SOC database schema upgraded to head.` 后继续；无需另跑建索引命令。
新空库发生一次瞬时 `disk I/O error` 时，启动器只清理本次失败产生的半库并安全
重试一次；调用前已经存在的数据库永远不会被自动删除或重建。

DeerFlow 与 SOC 分别使用 `deerflow.db` 和当前环境独立的 `soc_agent_dev.db` / `soc_agent_stg.db`，不得合并。SOC 的版本表是
`soc_alembic_version`；`alembic_version` 属于 DeerFlow 主库。迁移失败会发生在 Sidecar 启动之前，
命令直接返回失败，不会再表现为 `legacy-api exited during startup`。不要额外执行 `source`、
`unset SOC_DATABASE_URL` 或手工重复 migration。

## 6. Execution Plane Preflight / 执行面预检

项目自身提供 `4001` 模型网关、`8090` 旧 ZEUS 兼容 API、持久 Worker 和 Callback
Dispatcher；不得再启动 `$HOME/sec_know_model`、LiteLLM、Celery 或 Redis。

先运行不访问内网服务的 Fake E2E。它使用代码内置的无敏感合成协议夹具，不读取历史小 JSON、
PKL 或内网业务数据；它验证旧 HTTP 协议、SQLite migration、幂等、租约恢复、通用 Runtime、
结果投影、Outbox 与逐次回调审计，但固定标记 `simulated=true`：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
backend/.venv/bin/python backend/scripts/soc_pingan_legacy_fake_acceptance.py
```

报告必须为 `passed=true`，但不能据此关闭任何真实内网门禁。

确认私有配置和 EAGW key 已随包落位，且初始兼容模式仍为 `fake`：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
stat -f '%Lp %N' \\
  .env.soc-dev.local config.pingan-dev.local .secrets/eagw-private-key.der
grep -E '^export SOC_PINGAN_LEGACY_(LIFECYCLE|CALLBACK)_MODE=' \\
  .env.soc-dev.local
grep -E '^export (SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY|SOC_LLM_MAX_CONCURRENCY)=' \\
  .env.soc-dev.local
grep -n 'disable_streaming: true' config.pingan-dev.local
```

三个权限必须都是 `600`，两个 mode 必须都是 `fake`，两个并发值必须都是 `16`，并且模型配置必须
包含 `disable_streaming: true`。该设置让 EAGW 完整响应通过 LangChain 作为一个 buffered chat message
返回，避免聊天向 loopback gateway 发送不支持的 `stream=true`。初始项目 DEV 已同时激活 ZEUS PRD 和
Agent Platform PRD；私有 env 保存 ZEUS PRD/STG 两套受保护 profile，以及 Agent Platform PRD profile
和已确认的 STG endpoint。fake mode 不会发出生命周期查询或回调，切换 internal 后才允许真实调用。
RSA key 只存在于 private overlay，
不得复制到 source archive、Git 或验收报告。

{reset_section}

## 7. Start Host DEV / 启动服务

首次按本 Runbook 顺序执行到这里时，Host DEV 尚未启动。直接执行一次启动命令，再查看状态：

```bash
bash <<'BASH'
set -euo pipefail
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon --demo-no-auth
python3.12 scripts/soc_pingan_macos_host_dev.py status
BASH
```

只有本节曾经执行过、终端中断后回来继续验收时，才先单独运行 `status`。如果 Core 全部为 `true`、
三个 Sidecar 都为 `running`，且 `soc_database.status=ready`，不要重复 `start`，直接执行模型 Smoke；
否则重新执行上面的启动块。

按下面的状态决定下一步，不要重复执行已经通过的阶段：

| 看到的状态 | 下一步 |
|---|---|
| 尚未执行 `start` | 完成第 6 节预检，并按第 6.1 节确认本次数据处理方式后，执行本节启动块 |
| `status` 中 Core/Sidecars 全部运行，且 `soc_database.status=ready` | 不再建库或重启，直接执行模型 Smoke/后续验收 |
| `soc_database.status=schema_unavailable` | 查看同时输出的 `reason` 和 `error`；`database_busy` 表示读取时遇到锁，其他读取错误或版本表为空需按具体信息排查，不能仅凭该状态判断数据丢失或重新初始化 |
| `SOC database preparation failed before sidecar startup` | {database_recovery} |
| `legacy-api exited during startup` | 只可能来自旧交付包或非数据库启动错误；先查 Sidecar 日志，不要盲目删库 |
| `legacy-worker exited during startup` / `did not become ready` | Worker 的数据库、Runtime、Policy、ZEUS 或 Callback 初始化失败；查看 `backend/.deer-flow/internal-host-dev/sidecars/legacy-worker.log`，不得继续提交 30 分钟验收任务 |


Host DEV 驱动会先准备可复用的前端构建，再准备 SOC 数据库，启动项目自有 `4001` 模型网关、`8090` 兼容 API 和 Worker，最后启动
DeerFlow Gateway/Frontend/Nginx；同时启用隔离 SQLite、LLM analyzer、已评审 DEV Tenant
Policy 和两个 SOC DEV Workbench，关闭真实外部动作执行。仅本机使用时加 `--local-only`。
`status` 必须同时显示 `soc_database.status=ready`、
`soc_database.schema_revision=0033_pattern_revisions`，并且三个 Sidecar 都为 `running`。Worker 只有在数据库、
Runtime、Tenant Policy、ZEUS lifecycle 和 Callback 初始化完成并发布 PID-bound ready 信号后才会显示
`running`；`stale`、`not_running` 或启动时报 `did not become ready` 都不能继续真实验收。

前端默认使用预构建产物，点击页面时不再临时编译；首次或代码改变后启动需要等待一次编译，
后续不变的启动复用构建，不重新安装依赖。构建失败会在迁移/替换服务之前停止。
`status` 中 `frontend_mode_last_started=prebuilt` 表示上次成功启动采用预构建；
Gateway 的 DEV 热更新与 Memory/Policy 环境没有因此改为 STG。
仅外观开发需要热更新时，DEV 可在启动命令加 `--frontend-mode dev`；STG 拒绝此参数。
查询索引是可重建的派生数据，不替代原始结果；第一次进入历史运行较多的工作台可能需要补建索引，
不要把该准备时间视为每次导航的耗时，也不要为此删除业务数据库。

服务启动后执行无业务数据的真实模型 smoke：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
backend/.venv/bin/python backend/scripts/soc_pingan_model_gateway_smoke.py \\
  --confirm-live \\
  --report-path backend/.deer-flow/soc-internal-validation/model/model-gateway-smoke.json
```

基础连通性报告必须为 `outcome=passed`、`passed=true`、`thinking_requested=false`、
`max_tokens_requested=128`；它与当前 SOC Runtime 默认模式一致。usage 缺失时允许记录为不可用，
不得伪造 Token。Thinking 能力是独立的显式验收，不得用它阻塞基础连通性。

到这里，Web 演示、Fake E2E 和真实模型连通性已经完成。只需要页面演示或本地研判时在此停止，
**不需要提供告警 ID，也不要切换 internal Provider**。
本次“沉淀经验 / 验证效果”请直接进入第7.1节，不执行后面的 ZEUS 真实回写验收。

只有要验证真实旧 ZEUS `submit -> precheck -> Runtime -> status -> callback` 闭环时，才运行请求
准备器，并在提示后输入一个获批且当前仍处于“待审阅”的 ZEUS `alert_id`：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
backend/.venv/bin/python \\
  backend/scripts/soc_pingan_prepare_legacy_live_request.py \\
  --overwrite
```

脚本会从已落位且与索引 SHA-256 绑定的 Workbench payload SQLite 读取该 ID 的完整
`alert_full_data.alert_data`，校验快照状态和内外层 ID，自动写入 `app_code=zeus`、
`flow_id=alert_agent` 和新的 `session_id`，再以原子方式生成权限为 `0600` 的
`backend/.deer-flow/soc-internal-validation/legacy-compat/task-request.local.json`。不需要、也不允许
手工编辑请求 JSON；报告只输出 ID、Hash、大小和状态，不输出告警正文。

如果此前请求已经提交，而 SOC 数据库后来被删除或重建，旧 Job 的幂等与审计链已经丢失：不得复用
旧 `task-request.local.json`、旧 `session_id` 或 `--resume-existing`。只能由操作员批准另一条仍待审告警，
生成新请求。只有数据库和原 Job 均保留时，才允许按后文恢复命令继续同一请求。

`SOC_PINGAN_COMPAT_APP_KEYS_JSON` 的 value 构成旧协议允许的 Bearer/`app-key` 集合，映射标签
（当前为 `common`）不要求等于 `app_code`。随后用下面的可复制命令把两个 Provider mode 从
`fake` 切换为 `internal`，无需打开 `.env.soc-dev.local`：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
backend/.venv/bin/python \\
  backend/scripts/soc_pingan_set_legacy_provider_mode.py \\
  --mode internal
```

在启动完整 Worker、调用模型和创建新 Job 之前，先复用同一私有请求执行一次只读 ZEUS 生命周期
Smoke。它会发送与生产 Provider 完全相同的签名 JSON 字节，但不会提交本地任务、调用模型或触发回调：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
backend/.venv/bin/python backend/scripts/soc_pingan_zeus_lifecycle_smoke.py \\
  --confirm-live \\
  --request-file backend/.deer-flow/soc-internal-validation/legacy-compat/task-request.local.json \\
  --report-path backend/.deer-flow/soc-internal-validation/legacy-compat/lifecycle-smoke.json
```

只有报告同时满足 `outcome=pending`、`passed=true`、`provider_code=200`、
`provider_status=1`、`mocked=false`，才继续完整任务。`provider_code=40100` 表示签名/鉴权未通过；
此时立即停止，不消耗模型资源，并检查交付版本与内网 private overlay。报告只保留状态、业务码和响应
SHA-256，不保存 ZEUS 响应正文。

如果 Smoke 返回未知业务码，需要查看服务端完整 JSON，只运行下面的只读诊断。该文件可能包含内网
业务数据，仅保存在当前 Mac 的忽略目录，不得加入 Git、邮件或公开支持包：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
backend/.venv/bin/python \
  backend/scripts/soc_pingan_zeus_lifecycle_response_probe.py \
  --confirm-live \
  --overwrite \
  --request-file backend/.deer-flow/soc-internal-validation/legacy-compat/task-request.local.json
```

脚本在终端打印完整 Provider 响应，并以 `0600` 保存到
`backend/.deer-flow/soc-internal-validation/legacy-compat/lifecycle-response.local.json`。
业务码非 `200` 仍代表诊断请求成功，脚本退出码为 `0`；修复业务问题后必须重新运行前面的 Smoke，
不能用该完整响应代替 `pending` 门禁。

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py stop
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon --demo-no-auth
python3.12 scripts/soc_pingan_macos_host_dev.py status
```

确认 Core 全部为 `true`、三个 Sidecar 都为 `running`，且 SOC 数据库为 `ready` 后，首次提交这份
新请求：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
backend/.venv/bin/python backend/scripts/soc_pingan_legacy_live_acceptance.py \\
  --confirm-live \\
  --database-url "$SOC_DATABASE_URL" \\
  --request-file backend/.deer-flow/soc-internal-validation/legacy-compat/task-request.local.json \\
  --report-path backend/.deer-flow/soc-internal-validation/legacy-compat/live-acceptance.json
```

脚本会在连接 `8090` 前验证绝对 SQLite 路径、`soc_alembic_version` 和 Processing Job/Callback
表；本地证据库不可用时不会提交任务。如果前一次运行已提交同一请求，但客户端在读取本地证据或
生成报告时失败，并且已持久化的 lifecycle/Runtime/callback 本身均成功，**不要重新运行请求准备器，
也不要更换 `session_id`**。保留原
`task-request.local.json`，直接执行下面的恢复命令：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
backend/.venv/bin/python backend/scripts/soc_pingan_legacy_live_acceptance.py \\
  --confirm-live \\
  --resume-existing \\
  --database-url "$SOC_DATABASE_URL" \\
  --request-file backend/.deer-flow/soc-internal-validation/legacy-compat/task-request.local.json \\
  --report-path backend/.deer-flow/soc-internal-validation/legacy-compat/live-acceptance.json
```

恢复模式仍会提交两次完全相同的幂等请求并要求返回同一个 `task_id`，但会读取已有 Job 的终态、
Runtime lineage、生命周期事件和 Callback Outbox，不会创建第二个 Job，也不会重新运行已完成的模型
研判或回调。首次响应必须已经进入 `STARTED/SUCCESS/FAILURE` 才能证明是既有任务；若仍为
`PENDING`，保持请求不变并稍后再次执行恢复命令。

如果旧 Job 已记录 `lifecycle_state=unknown`、Callback `dead_letter`，或者修复了签名/Provider/Worker
代码，旧失败 Job 不能通过 `--resume-existing` 变成成功证据：其历史事件和 attempt 是不可变审计。
先让只读生命周期 Smoke 通过，再用请求准备器生成新的 `session_id` 和新 Job；原失败 Job 保留用于
复盘，不删除数据库。

只有报告同时满足 `outcome=passed`、`fresh_submission_confirmed=true` **或**
`resumed_existing_confirmed=true`，并且
`idempotent_replay_confirmed=true`、`run_id_present=true`、`lifecycle_mocked=false`、
`callback_status=delivered`、`callback_mocked=false` 和
`proves_real_internal_connectivity=true`，才证明本机兼容入口、真实 ZEUS precheck、Runtime/LLM 与
callback transport 的组合连通。脚本不会输出告警正文、App Key 或回调正文。最终旧系统兼容门禁还
必须由 ZEUS 上游真实发起 `POST /workflow/task`，并在旧 ZEUS 页面核对返回的 Job、回写结果和任务状态；
本机自提交不能替代这一步。如果 ZEUS callback 要求上游先登记 `taskId`，本机自提交的 callback 拒绝
属于组件验收边界，不应伪装为完整业务闭环。如果真实 ZEUS precheck 表明这条快照告警已经被处理，验收会在
模型调用前 fail closed；重新运行请求准备器并输入另一个当前待审阅 ID 即可。

单条真实验收结束且暂不继续测试时，用下面命令恢复 fake Provider 并重启，避免后续演练误触真实回调：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
backend/.venv/bin/python \\
  backend/scripts/soc_pingan_set_legacy_provider_mode.py \\
  --mode fake
python3.12 scripts/soc_pingan_macos_host_dev.py stop
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon --demo-no-auth
```

`--demo-no-auth` 仅用于可信内网演示：页面跳过注册/登录，所有访问者共享一个合成管理员身份，
因此不能区分个人审计 actor。需要验收账号与权限时，先停止服务，再去掉该参数启动；无需改代码或数据库：

Host DEV 默认允许最多 16 条不同告警并行研判；同一告警的重复点击不会再次进入 Runtime/LLM。
部署 Mac 本机打开 `http://localhost:2026/workspace/soc/corpus-validation`，进入“运行设置”，
在“最大并发”中选择 `1～16` 并点击“保存并发”。保存后直接生效，无需重启或重新准备批次；
第一批、第二批、单条运行和后台经验起草共用此上限。调低时让已开始的任务完成，再按新上限补位；
调高后按新上限领取后续任务。暂停中的批次仍保持暂停，需要继续时点击“继续”。
同事通过局域网可看到新上限并运行、暂停，但不能修改并发。设置随数据库同目录的小型配置文件保存，
服务重启及保留数据升级后继续生效；不改变已完成结果、已审核经验、排队任务或匹配规则。
部署配置中的 `SOC_LLM_MAX_CONCURRENCY` 和 `SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY` 仍保持 `16`，
无需为页面调节而修改 env；后者是聊天与研判共享的最终容量门。
SQLite 下的 `SOC_PINGAN_LEGACY_WORKER_CONCURRENCY` 仍固定为 `1`，仅控制 ZEUS 持久任务取件速度。
同类组顺序和共享模型容量可能使实际运行数小于设定上限；页面显示当前占用与上限。
{capacity_instruction}

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py stop
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon
```

```bash
curl -fsS http://localhost:2026/health
```

演示模式首次打开 `http://localhost:2026` 会直接进入工作区，不创建账号。
常用页面：

```text
http://localhost:2026/workspace/soc/operations
http://localhost:2026/workspace/soc/review
http://localhost:2026/workspace/soc/memory
http://localhost:2026/workspace/soc/corpus-validation
```

停止：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py stop
```

## 7.1 Two-Batch Memory Evaluation / 两批经验验证

模型 Smoke 通过后，在部署 Mac 打开 `http://localhost:2026/workspace/soc/corpus-validation`。
完整网页操作见 `.notes/ai_soc/integrations/pingan-corpus-batch-runbook.md`；运行设置只能在部署 Mac 本机修改。
{batch_start_instruction}
{batch_restart_instruction}

1. 第一批尚未开始时，网页第一批点击“开始积累”；第8节内完成执行失败、语义核对失败补跑，再按第9节审核经验。已有完成的第一批不重复执行。
2. 第10节启动第二批，语义核对开启，其余三项策略关闭；第11节处理失败并保存首次报告。两批分别保存设置，点“继续”沿用已排队任务原配置。
3. 第12节仅在需要时接续保存结果，不启动第二批；第13节页面审核新增经验，再重新配置第二批复测。
4. 第14节导出新复测报告，生成精简统计并复制给外网总结。不同经验快照的两轮分别保留报告，不合并。

第二批接续第一批核心行为积累，新候选待审核；本轮使用开始时冻结的已审核经验，验证期间不修改经验。
历史语料任务不查询/回写 ZEUS，不执行真实处置。只做两批验证时不执行附录A、B的真实集成验收。
静态粗分组只用于名单，不运行全量语义事实预处理；实际经验按核心行为细分积累。
第一批9,364条、第二批主要验证2,153条、补充3,764条，合计15,281条。
原第一批2,997条全部保留在学习侧；仅从原主要验证8,520条中移入6,367条，补充3,764条成员不变。
只有实际研判时才按运行设置执行语义核对；旧实验的任务和历史结果不会自动算作新名单已完成。
原始15,288条语料保留，7条 `RPAADM_002192` SIEM 邮件告警不参加验证；旧4,343条载荷库不能混用新版索引。

{operations_steps}

## 附录A. Promote Runtime To STG / 切换到 STG（不属于语料验证流程）

DEV 页面、模型和真实 Provider 验收通过后，可以在同一份代码上切到隔离 STG 部署 profile。该命令原子
应用项目 STG -> ZEUS STG + Agent Platform STG 映射：Memory、Tenant Policy、Automation 和 SOC SQLite 会一起切到 `stg`，数据库使用
`backend/.deer-flow/data/soc_agent_stg.db`；DEV 语料/Memory Workbench 和免登录演示会关闭。
STG 同时改用 DeerFlow 原生 `--prod` 优化服务模式，不启用 Next.js HMR；第一次启动会构建前端，
后续每次切换仍以当前代码重新构建，避免复用旧版本页面。

切换命令会从 mode-`0600` 私有 env 读取 ZEUS 和 Agent Platform 的 STG profile，并激活各自的
endpoint、allowlist、应用凭证和 workflow ID，不要求手工改 URL/Key。旧源码只证明 Agent Platform STG
endpoint，并未提供 STG 的 `YHSYS` 身份和三个 workflow ID；如果以下五项尚未由内网负责人补齐，切换会
在修改 env 前 fail closed：

```text
SOC_PINGAN_WORKFLOW_STG_APP_ID
SOC_PINGAN_WORKFLOW_STG_APP_SECRET
SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID
SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID
SOC_PINGAN_WORKFLOW_STG_USER_ID
```

不得使用旧源码中的其他应用身份替代 `YHSYS`。模型 EAGW、lifecycle/callback Provider mode 和真实外部
动作权限仍保持独立，不会被环境切换命令改写。为避免把 DEV
联调时遗留的真实回写模式带入 STG，下面先显式恢复两个旧兼容 Provider 为 `fake`：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
backend/.venv/bin/python \\
  backend/scripts/soc_pingan_set_legacy_provider_mode.py \\
  --mode fake
python3.12 scripts/soc_pingan_macos_host_dev.py stop
backend/.venv/bin/python \\
  backend/scripts/soc_pingan_set_runtime_environment.py \\
  --environment stg
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon
python3.12 scripts/soc_pingan_macos_host_dev.py status
```

切换报告必须显示 `environment=stg`、`database_filename=soc_agent_stg.db`、
`workbenches_enabled=false`、`demo_no_auth_allowed=false`、`zeus_target_environment=stg`、
`agent_platform_target_environment=stg` 和 `runtime_target_mapping_applied=true`。状态报告必须显示
`runtime_environment=stg`、`zeus_target_matches_runtime=true`、
`agent_platform_target_matches_runtime=true`、`service_mode=production_optimized`、STG 数据库
`status=ready`，并且 Core/Sidecars 正常。STG 启动禁止
添加 `--demo-no-auth`；外部动作仍固定关闭，之后如需真实 lifecycle/callback，只能再走独立的 Provider
mode 验收步骤。

如需回到演练 DEV，先停服务，再执行相同命令的 `--environment dev`；它会同时恢复项目 DEV -> ZEUS
PRD + Agent Platform PRD 映射，随后可用 `start --daemon --demo-no-auth` 启动。DEV 和 STG 的 SOC 数据不会混库。

## 附录B. Real Integration Follow-up / 真实验收顺序（不属于语料验证流程）

完成模型与生命周期连通性后，先用旧实现中的三个示例值做只读发现。它们只是查找候选，不是当前资产
归属真值；只有核对 Provider attempts 和内网返回后，才能写入 mode-`0600` 的 D12-B 七用例矩阵：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local

backend/.venv/bin/python backend/scripts/soc_pingan_asset_direct_smoke.py \\
  --confirm-live \\
  --query "10.12.31.24" \\
  --asset-type IP \\
  --role victim \\
  --report-path backend/.deer-flow/soc-internal-validation/d12b/discovery-ip.json

backend/.venv/bin/python backend/scripts/soc_pingan_asset_direct_smoke.py \\
  --confirm-live \\
  --query "SZC-L0649671" \\
  --asset-type HOST \\
  --role victim \\
  --report-path backend/.deer-flow/soc-internal-validation/d12b/discovery-host.json

backend/.venv/bin/python backend/scripts/soc_pingan_asset_direct_smoke.py \\
  --confirm-live \\
  --query "zhangjianming627" \\
  --asset-type USER \\
  --um "zhangjianming627" \\
  --role owner \\
  --report-path backend/.deer-flow/soc-internal-validation/d12b/discovery-um.json
```

```text
D12-B preflight
  -> model gateway completion smoke
  -> read-only ZEUS lifecycle/signature smoke
  -> local legacy 8090 submit/status/precheck/callback acceptance
  -> ZEUS-originated submit + old-page readback
  -> ZEUS asset direct smoke
  -> MCP asset.locate smoke
  -> InvestigationEvidence 回读
  -> TI / Security Tag 真实只读 smoke
  -> 5 条真实告警
  -> 50 条 resume
  -> 200 / 5000+ 条 shadow
```

详细命令以解压后的以下文档为准：

```text
.notes/ai_soc/integrations/pingan-internal-continuation-handoff.md
backend/samples/pingan_dev/README.md
validation/compact_zeus/internal_batch/README.md
```

不要把凭证、原始内部告警、Memory 数据库或完整 Provider 响应传回外网。

"""


def _source_readme(timestamp: str, *, worktree_dirty: bool) -> str:
    source_state = (
        "This development-only archive was explicitly built from a dirty worktree. "
        "It is not eligible for final internal handoff; rebuild from a clean commit."
        if worktree_dirty
        else "This archive was built from a clean committed worktree and is eligible for handoff review."
    )
    return f"""# PingAn Internal Source Bundle

Built at `{timestamp}`. {source_state}

It excludes credentials, private PKL/XLSX, generated SQLite, virtual environments,
Node dependencies, Git metadata, static knowledge snapshots, archived notes, and
large demo media.

Extract this archive first. Then extract the separately protected private-overlay
archive into the same parent directory. Read:

- `.notes/ai_soc/integrations/pingan-internal-continuation-handoff.md`
- `backend/samples/pingan_dev/README.md`
- `validation/compact_zeus/internal_batch/README.md`
- `validation/compact_zeus/e2e/README.md`

On an internal Mac that already has Python 3.12+, uv, Node 22+, the pinned pnpm,
and nginx, use the native no-Docker Host DEV driver. It installs from the approved
internal package registries and starts without repeating dependency resolution:

```bash
python3.12 scripts/soc_pingan_stage_internal_corpus.py
python3.12 scripts/soc_pingan_stage_internal_corpus.py --apply
python3.12 scripts/soc_pingan_macos_host_dev.py check
python3.12 scripts/soc_pingan_macos_host_dev.py install
python3.12 scripts/soc_pingan_macos_host_dev.py start --demo-no-auth
```

The staging commands verify the separately supplied files under
`$HOME/Downloads/source` and `$HOME/Downloads/corpus` against the protected
manifest/index before copying them into the checkout. Large PKL and Workbench
payload SQLite files are not part of either transfer archive.

`--demo-no-auth` 仅用于可信 DEV 演示；全部访问者共享一个合成管理员身份。正式身份与权限验收时去掉该参数。
告警演练默认支持最多 16 条不同告警并行，同一告警由服务端防重；部署 Mac 本机可在运行设置中
选择 `1～16` 并保存，已有的较低设置保留，无需修改 env 或重启。
`.env.soc-dev.local` 的 `SOC_LLM_MAX_CONCURRENCY` 与
`SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY` 保持 `16`。PingAn model profile 使用
`disable_streaming: true` 适配 EAGW 的完整响应，聊天可用但不提供 token-by-token 输出。

The separately transferred Apple Silicon offline toolchain remains a fallback
for a Mac without a usable Python/uv or internal Python package registry:

```bash
tar -xzf deer-flow-pingan-macos-arm64-offline-<timestamp>.tar.gz
deer-flow-pingan-macos-arm64-offline/install-offline.sh /absolute/path/to/deer-flow
```

Do not use a public Python or NPM registry inside the restricted network. The
authoritative commands and package verification steps are in
`.notes/ai_soc/integrations/pingan-internal-continuation-handoff.md`.
"""


def _private_readme(timestamp: str) -> str:
    return f"""# PingAn Internal Private Overlay

Built at `{timestamp}`. This archive contains local credentials/configuration and
reviewed tenant artifacts, including corpus manifests/index but not the three large
PKL files or Workbench payload SQLite. It is intentionally separate from source
control and must remain mode `0600`, inside the approved environment only.

Extract it over the source bundle's `deer-flow-pingan-internal/` directory. Do
not commit its contents. Then run `scripts/soc_pingan_stage_internal_corpus.py`
against the separately supplied files before Host DEV check/install. Do not copy
generated batch artifacts back outside the approved environment without review.
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--include-private-overlay", action="store_true")
    parser.add_argument(
        "--initialize-soc-dev",
        action="store_true",
        help="Select the reinitialization guide as default; always writes both guides and never resets a database",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Build a development-only archive from a dirty worktree; never use for final handoff",
    )
    parser.add_argument(
        "--inspect", type=Path, help="Inspect one existing archive instead of building"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.inspect:
            report = inspect_archive(args.inspect)
        else:
            report = build_transfer_archives(
                output_dir=args.output_dir,
                include_private_overlay=args.include_private_overlay,
                allow_dirty=args.allow_dirty,
                initialize_soc_dev=args.initialize_soc_dev,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (
        OSError,
        subprocess.CalledProcessError,
        tarfile.TarError,
        ValueError,
    ) as exc:
        print(f"error: {str(exc)[:1000] or type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
