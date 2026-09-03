#!/usr/bin/env python3
"""Build separate source and private-overlay archives for PingAn internal DEV."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRANSFER_ROOT = ROOT / "backend/.deer-flow/internal-transfer"
DEFAULT_OUTPUT_DIR = TRANSFER_ROOT / "READY-TO-TRANSFER"
TRANSFER_INSTALLER_NAME = "INSTALL-PINGAN-MAC.sh"
TRANSFER_RUNBOOK_NAME = "PINGAN-INTERNAL-MAC-RUNBOOK.md"
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
    "backend/soc_agent/db/models.py",
    "backend/soc_agent/integrations/pingan/agent_platform_target.py",
    "backend/soc_agent/integrations/pingan/agent_workflow.py",
    "backend/soc_agent/integrations/pingan/asset_location.py",
    "backend/soc_agent/integrations/pingan/legacy_model_gateway_profile.py",
    "backend/soc_agent/integrations/pingan/legacy_workflow_profile.py",
    "backend/soc_agent/integrations/pingan/model_gateway.py",
    "backend/soc_agent/integrations/pingan/model_gateway_smoke.py",
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
    "scripts/soc_pingan_macos_host_dev.py",
    "scripts/soc_pingan_host_sidecars.py",
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
) -> dict[str, Any]:
    git_info = _git_info(root)
    _assert_source_freeze_allowed(git_info, allow_dirty=allow_dirty)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
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

    if include_private_overlay:
        assert private_paths is not None
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

    report_path = output_dir / f"transfer-report-{timestamp}.json"
    runbook_path = output_dir / TRANSFER_RUNBOOK_NAME
    _write_private_text(
        runbook_path,
        _transfer_runbook(
            timestamp=timestamp,
            git_info=git_info,
            archives=archives,
            report_name=report_path.name,
            installer=installer,
        ),
    )
    report = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "output_directory": str(output_dir),
        "archives": archives,
        "installer": installer,
        "runbook": _sidecar_result(runbook_path),
        "source_worktree_dirty": git_info["worktree_dirty"],
        "required_source_file_count": len(REQUIRED_HANDOFF_SOURCE_PATHS),
        "required_source_inventory_complete": True,
        "dirty_override_used": bool(git_info["worktree_dirty"] and allow_dirty),
        "final_handoff_eligible": not git_info["worktree_dirty"],
        "secrets_in_console_output": False,
    }
    _write_private_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


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
        "SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY": "3",
        "SOC_ANALYZER_MODE": "llm",
        "SOC_LLM_MODEL": "deepseek-v4-flash",
        "SOC_LLM_MAX_CONCURRENCY": "3",
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
    return f"""# PingAn Internal Mac DEV/STG Runbook / 平安内网 Mac DEV/STG 操作手册

> Built: `{timestamp}`
> Source commit: `{git_info["commit"]}` (`{git_info["branch"]}`)
> Target: Apple Silicon macOS, Python `3.12+`, no Docker
> Install path: `$HOME/deer-flow`

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
{installer_name}
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

必须看到 `source_worktree_dirty=false`、`final_handoff_eligible=true` 和
`required_source_inventory_complete=true`。

## 3. Install Or Data-Preserving Redeploy / 安装或保留数据升级

以下命令会替换当前用户的 `$HOME/deer-flow` 代码；已有部署会自动保留明确列入契约的运行数据：

```text
backend/.deer-flow/data/                 # deerflow.db、soc_agent_dev.db、soc_agent_stg.db 及 SQLite sidecars
backend/.deer-flow/.jwt_secret           # 已有登录会话签名密钥
backend/.deer-flow/users|agents|threads  # 用户 Memory、自定义 Agent 与工作区
backend/.deer-flow/integrations          # 已安装的受管 Integration Skills
backend/.deer-flow/soc-internal-validation
```

新 private overlay 中的配置、凭证与 `pingan-context` 仍以本次交付为准；旧 PID、日志、技能投影、
临时缓存和旧安装包不会迁入新 checkout。

```bash
bash "$HOME/READY-TO-TRANSFER/{installer_name}"
```

安装器只读取它自身所在目录，不依赖前一节留下的 `TRANSFER_DIR`、`TARGET_REPO` 或其他
shell 状态。它在子 Bash 中依次校验准确 SHA-256、解压并检查新 checkout、停止旧 Host DEV、
确认 `3000/8001/2026/4001/8090` 全部释放，再事务式替换目录、复制允许的持久化状态并设置私有文件权限。任一步失败
都只结束安装器，不会关闭当前终端；Hash/解压/停服/端口失败时不会替换旧 checkout，替换阶段
或数据恢复失败时会尽力恢复旧目录。不要使用 `source` 或 `.` 加载安装器。

正常重部署不要删除 `deerflow.db`、`soc_agent_dev.db` 或 `soc_agent_stg.db`。只有首次初始化从未成功、且已确认库内没有
账号、研判、Memory、审核或任务数据时，才可把残库和 `-wal`/`-shm`/`-journal` 一并移动到带时间戳
的隔离备份目录后重新初始化；仍不要直接 `rm`。

### 3.1 Stateless DEV Reset / 无状态 DEV 清洁重装

如果安装器报告 `existing target is not a recognized Host DEV checkout`，说明
`$HOME/deer-flow` 存在，但它不是当前安装器能够安全停服和保留状态的完整旧部署。停止端口进程并不会
消除这个目录检查。仅当已明确确认该目录只是废弃部署包，而且其中的旧 SQLite、Memory、账号和内网验收结果都会永久删除时，
才执行下面的清洁重装。正常升级禁止使用本节。

下面的命令会先展示 `3000/8001/2026/4001/8090` 的监听者，只有输入完整确认短语
`DELETE-OLD-DEV` 后，才终止这些 DEV 端口上的残留进程并删除固定目标 `$HOME/deer-flow`：

```bash
bash <<'BASH'
TARGET_REPO="$HOME/deer-flow"

printf 'Target to delete: %s\n' "$TARGET_REPO"
for port in 3000 8001 2026 4001 8090; do
  printf '\n===== TCP %s =====\n' "$port"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
done

read -r -p 'Type DELETE-OLD-DEV to permanently remove this stateless DEV deployment: ' confirmation </dev/tty
if [[ "$confirmation" != "DELETE-OLD-DEV" ]]; then
  echo 'Cancelled; no process or file was changed.'
else
  pids="$(
    for port in 3000 8001 2026 4001 8090; do
      lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null
    done | sort -u
  )"
  if [[ -n "$pids" ]]; then
    kill -TERM $pids 2>/dev/null || true
    sleep 3
  fi

  remaining="$(
    for port in 3000 8001 2026 4001 8090; do
      lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null
    done | sort -u
  )"
  if [[ -n "$remaining" ]]; then
    kill -KILL $remaining 2>/dev/null || true
    sleep 1
  fi

  remaining="$(
    for port in 3000 8001 2026 4001 8090; do
      lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null
    done | sort -u
  )"
  if [[ -n "$remaining" ]]; then
    echo "Refusing to delete while DEV ports remain occupied: $remaining"
  else
    /bin/rm -rf "$TARGET_REPO"
    if [[ ! -e "$TARGET_REPO" ]]; then
      echo "Old stateless DEV deployment removed: $TARGET_REPO"
    else
      echo "Removal failed; inspect permissions before retrying."
    fi
  fi
fi
BASH
```

看到 `Old stateless DEV deployment removed` 后，重新执行本节上方的
`bash "$HOME/READY-TO-TRANSFER/{installer_name}"`。新部署会从空库开始，不恢复任何旧运行状态。

本次私有包同时包含：

```text
.secrets/eagw-private-key.der
validation/compact_zeus/data/corpus/full_alert_validation_corpus.manifest.json
validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.manifest.json
validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json
```

三个 PKL 和 Workbench payload SQLite 不在 private overlay；它们已单独保存在内网
`$HOME/Downloads/source` 与 `$HOME/Downloads/corpus`，必须按下一节校验并落位。

## 4. Stage Existing Corpus / 落位内网已有语料

当前开发者的 `$HOME` 会自然解析为 `/Users/zhangjianming627`；其他同事无需修改脚本。
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
完成后语料 Workbench 可直接使用，不需要在内网重建 4343 条索引。

## 5. Host Check And Install / 主机检查与依赖安装

前置要求：Apple Silicon macOS、Python `3.12+`、uv、Node `22+`、项目固定的
pnpm、nginx `1.23+`，以及已配置的平安 PyPI/pnpm 镜像。

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py check
python3.12 scripts/soc_pingan_macos_host_dev.py install
```

安装器使用冻结 lock、内部镜像和独立 `backend/.venv`。不要执行 `uv lock`，
也不要让 pnpm/Python 访问公网。

不再手工初始化 SOC SQLite。Host DEV `start` 统一负责 SOC SQLite migration：它先从当前 checkout
根据 `SOC_PINGAN_ENV` 解析绝对 `soc_agent_dev.db` 或 `soc_agent_stg.db` 路径，在任何 Sidecar 和 Web 服务启动前升级 Schema，并把 Sidecar 的
重复自动迁移关闭。新空库发生一次瞬时 `disk I/O error` 时，启动器只清理本次失败产生的半库并安全
重试一次；调用前已经存在的数据库永远不会被自动删除或重建。

DeerFlow 与 SOC 分别使用 `deerflow.db` 和当前环境独立的 `soc_agent_dev.db` / `soc_agent_stg.db`，不得合并。SOC 的版本表是
`soc_alembic_version`；`alembic_version` 属于 DeerFlow 主库。迁移失败会发生在 Sidecar 启动之前，
命令直接返回失败，不会再表现为 `legacy-api exited during startup`。不要额外执行 `source`、
`unset SOC_DATABASE_URL` 或手工重复 migration。

按下面的状态决定下一步，不要重复执行已经通过的阶段：

| 看到的状态 | 下一步 |
|---|---|
| 尚未执行 `start` | 先完成下一节 Fake E2E，再执行第 7 节 `start` |
| `status` 中 Core/Sidecars 全部运行，且 `soc_database.status=ready` | 不再建库或重启，直接执行模型 Smoke/后续验收 |
| `SOC database preparation failed before sidecar startup` | 服务尚未启动；保留已有数据库排查。仅确认是无业务数据的新建残库时，才使用第 3.1 节清洁重装 |
| `legacy-api exited during startup` | 只可能来自旧交付包或非数据库启动错误；先查 Sidecar 日志，不要盲目删库 |
| `legacy-worker exited during startup` / `did not become ready` | Worker 的数据库、Runtime、Policy、ZEUS 或 Callback 初始化失败；查看 `backend/.deer-flow/internal-host-dev/sidecars/legacy-worker.log`，不得继续提交 30 分钟验收任务 |

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

三个权限必须都是 `600`，两个 mode 必须都是 `fake`，两个并发值必须都是 `3`，并且模型配置必须
包含 `disable_streaming: true`。该设置让 EAGW 完整响应通过 LangChain 作为一个 buffered chat message
返回，避免聊天向 loopback gateway 发送不支持的 `stream=true`。初始项目 DEV 已同时激活 ZEUS PRD 和
Agent Platform PRD；私有 env 保存 ZEUS PRD/STG 两套受保护 profile，以及 Agent Platform PRD profile
和已确认的 STG endpoint。fake mode 不会发出生命周期查询或回调，切换 internal 后才允许真实调用。
RSA key 只存在于 private overlay，
不得复制到 source archive、Git 或验收报告。

## 7. Start Host DEV / 启动服务

首次按本 Runbook 顺序执行到这里时，Host DEV 尚未启动。直接执行一次启动命令，再查看状态：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon --demo-no-auth
python3.12 scripts/soc_pingan_macos_host_dev.py status
```

只有本节曾经执行过、终端中断后回来继续验收时，才先单独运行 `status`。如果 Core 全部为 `true`、
三个 Sidecar 都为 `running`，且 `soc_database.status=ready`，不要重复 `start`，直接执行模型 Smoke；
否则重新执行上面的启动块。

Host DEV 驱动会先准备 SOC 数据库，再启动项目自有 `4001` 模型网关、`8090` 兼容 API 和 Worker，最后启动
DeerFlow Gateway/Frontend/Nginx；同时启用隔离 SQLite、LLM analyzer、已评审 DEV Tenant
Policy 和两个 SOC DEV Workbench，关闭真实外部动作执行。仅本机使用时加 `--local-only`。
`status` 必须同时显示 `soc_database.status=ready`、
`soc_database.schema_revision=0027_processing_jobs`，并且三个 Sidecar 都为 `running`。Worker 只有在数据库、
Runtime、Tenant Policy、ZEUS lifecycle 和 Callback 初始化完成并发布 PID-bound ready 信号后才会显示
`running`；`stale`、`not_running` 或启动时报 `did not become ready` 都不能继续真实验收。

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

Host DEV 默认允许 3 条不同告警并行研判；同一告警的重复点击不会再次进入 Runtime/LLM。
`SOC_LLM_MAX_CONCURRENCY` 和 `SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY` 必须同步调整，后者是所有
聊天与研判共享的最终容量门。SQLite 下的 `SOC_PINGAN_LEGACY_WORKER_CONCURRENCY` 仍固定为 `1`，它只
控制 ZEUS 持久任务取件速度，不限制前端告警演练并发。

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

## 8. Promote Runtime To STG / 切换到 STG

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

## 9. Real Integration Follow-up / 真实验收顺序

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
告警演练默认支持 3 条不同告警并行，同一告警由服务端防重；调整容量时必须同步修改
`.env.soc-dev.local` 的 `SOC_LLM_MAX_CONCURRENCY` 与
`SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY`。PingAn model profile 使用
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
