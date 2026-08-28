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
    {".db", ".key", ".pem", ".pkl", ".sqlite", ".sqlite3", ".xlsx"}
)
PRIVATE_OVERLAY_PATHS = (
    ".env.soc-dev.local",
    "config.pingan-dev.local",
    "validation/original_works/raw_program/Deepseek_Qwen_32B_EDR_Analysis_Ignored_Paths_Sup (1).xlsx",
    "datas/source/full_alert_2026_month_forth_sample_200.pkl",
    "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl",
    "validation/compact_zeus/data/corpus/full_alert_validation_corpus.manifest.json",
    "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl",
    "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.manifest.json",
    "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json",
    "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite",
    "backend/.deer-flow/pingan-context/software-path-catalog.sqlite",
    "backend/.deer-flow/pingan-context/software-path-catalog.build-report.json",
)
PRIVATE_ENV_REQUIRED_KEYS = frozenset(
    {
        "PINGAN_LITELLM_BASE_URL",
        "PINGAN_LITELLM_API_KEY",
        "PINGAN_LITELLM_MODEL",
        "SOC_PINGAN_ENV",
        "SOC_PINGAN_ASSET_PROVIDER_MODE",
        "SOC_PINGAN_ZEUS_BASE_URL",
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS",
        "SOC_PINGAN_ZEUS_APP_ID",
        "SOC_PINGAN_ZEUS_APP_KEY",
        "D12B_INVALID_ZEUS_APP_KEY",
        "D12B_TIMEOUT_ZEUS_BASE_URL",
        "D12B_TIMEOUT_ZEUS_ALLOWED_HOSTS",
        "SOC_PINGAN_WORKFLOW_ENV",
        "SOC_PINGAN_WORKFLOW_BASE_URL",
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS",
        "SOC_PINGAN_WORKFLOW_APP_ID",
        "SOC_PINGAN_WORKFLOW_APP_SECRET",
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID",
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID",
        "SOC_PINGAN_WORKFLOW_USER_ID",
    }
)
PRIVATE_ENV_OBSOLETE_KEYS = frozenset(
    {
        "env_profile",
        "SOC_PINGAN_PROVIDER_IMPORT_PATHS",
        "SOC_PINGAN_ZEUS_SIGNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_RUNNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_OPERATOR",
    }
)
_SHELL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REQUIRED_HANDOFF_SOURCE_PATHS = (
    ".notes/ai_soc/delivery-roadmap.md",
    ".notes/ai_soc/integrations/README.md",
    ".notes/ai_soc/integrations/mock-and-real-register.md",
    ".notes/ai_soc/integrations/pingan-dev-information-collection.md",
    ".notes/ai_soc/integrations/pingan-internal-continuation-handoff.md",
    "AGENTS.md",
    "backend/samples/pingan_dev/README.md",
    "backend/samples/pingan_dev/config.example.yaml",
    "backend/samples/pingan_dev/d12b-test-cases.example.yaml",
    "backend/samples/pingan_dev/env.example",
    "backend/samples/pingan_dev/extensions.example.json",
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
    "backend/scripts/soc_pingan_litellm_smoke.py",
    "backend/scripts/soc_pingan_security_tag_mcp_server.py",
    "backend/scripts/soc_pingan_threat_intel_mcp_server.py",
    "backend/scripts/soc_pingan_prepare_legacy_workflow_profile.py",
    "backend/soc_agent/integrations/pingan/agent_workflow.py",
    "backend/soc_agent/integrations/pingan/asset_location.py",
    "backend/soc_agent/integrations/pingan/legacy_workflow_profile.py",
    "backend/soc_agent/integrations/pingan/litellm_smoke.py",
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
    "scripts/test_build_pingan_macos_offline_bundle.py",
    "scripts/test_soc_pingan_macos_host_dev.py",
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

    report_path = output_dir / f"transfer-report-{timestamp}.json"
    runbook_path = output_dir / TRANSFER_RUNBOOK_NAME
    _write_private_text(
        runbook_path,
        _transfer_runbook(
            timestamp=timestamp,
            git_info=git_info,
            archives=archives,
            report_name=report_path.name,
        ),
    )
    report = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "output_directory": str(output_dir),
        "archives": archives,
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


def _transfer_runbook(
    *,
    timestamp: str,
    git_info: dict[str, Any],
    archives: dict[str, Any],
    report_name: str,
) -> str:
    source = archives["source"]
    private = archives.get("private_overlay")
    source_name = Path(str(source["path"])).name
    source_sha = str(source["sha256"])
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
            "The private overlay contains local configuration, credentials, private alert "
            "corpora, the workbench index/payload store, and the reviewed EDR path catalog. "
            "Keep the directory inside the approved environment."
        )
    return f"""# PingAn Internal Mac DEV Runbook / 平安内网 Mac DEV 操作手册

> Built: `{timestamp}`
> Source commit: `{git_info["commit"]}` (`{git_info["branch"]}`)
> Target: Apple Silicon macOS, Python `3.12+`, no Docker
> Install path: `$HOME/deer-flow`

本手册由 `scripts/build_pingan_internal_transfer.py` 随包生成。文件名、commit 和
SHA-256 与本次交付一致，不需要额外 nginx/LAN hotfix。

## 1. Package Identity / 包身份

`READY-TO-TRANSFER` 应只保留本次交付的以下文件：

```text
{source_name}
{private_name}
{report_name}
{TRANSFER_RUNBOOK_NAME}
```

SHA-256：

```text
{source_sha}  {source_name}
{private_sha}  {private_name}
```

{private_notice}

## 2. Verify / 传输后校验

```bash
export TRANSFER_DIR="$HOME/READY-TO-TRANSFER"
cd "$TRANSFER_DIR"

shasum -a 256 \
  "{source_name}" \
  "{private_name}"
```

输出必须与第 1 节完全一致。随后检查 report：

```bash
cat "{report_name}"
```

必须看到 `source_worktree_dirty=false`、`final_handoff_eligible=true` 和
`required_source_inventory_complete=true`。

## 3. Clean Install / 全新安装

以下命令会替换当前用户的 `$HOME/deer-flow`：

```bash
export TARGET_REPO="$HOME/deer-flow"
export STAGE_DIR="$TRANSFER_DIR/extract"

[ "$TARGET_REPO" = "$HOME/deer-flow" ] || exit 1
rm -rf "$TARGET_REPO" "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

tar -xzf "$TRANSFER_DIR/{source_name}" -C "$STAGE_DIR"
tar -xzf "$TRANSFER_DIR/{private_name}" -C "$STAGE_DIR"
mv "$STAGE_DIR/deer-flow-pingan-internal" "$TARGET_REPO"
cd "$TARGET_REPO"

chmod 600 .env.soc-dev.local config.pingan-dev.local
```

本次私有包同时包含：

```text
datas/source/full_alert_2026_month_forth_sample_200.pkl
validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl
validation/compact_zeus/data/corpus/full_alert_validation_corpus.manifest.json
validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl
validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.manifest.json
validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json
validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite
```

因此语料 Workbench 不需要在内网重新生成 4343 条索引。

## 4. Host Check And Install / 主机检查与依赖安装

前置要求：Apple Silicon macOS、Python `3.12+`、uv、Node `22+`、项目固定的
pnpm、nginx `1.23+`，以及已配置的平安 PyPI/pnpm 镜像。

```bash
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py check
python3.12 scripts/soc_pingan_macos_host_dev.py install
```

安装器使用冻结 lock、内部镜像和独立 `backend/.venv`。不要执行 `uv lock`，
也不要让 pnpm/Python 访问公网。

初始化 SOC SQLite：

```bash
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
unset SOC_DATABASE_URL

(
  cd backend
  .venv/bin/python -m soc_agent.cli db upgrade
)
```

DeerFlow 与 SOC 分别使用 `deerflow.db` 和 `soc_agent_dev.db`，不得合并。

## 5. Internal Model / 内网模型

在 `$HOME/sec_know_model` 的独立终端按现有顺序启动：

```bash
bash ./start_proxy_.sh
bash ./local_run.sh
./.venv/bin/python ./run.py
```

确认 LiteLLM `4001` 可用后执行：

```bash
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local

backend/.venv/bin/python backend/scripts/soc_pingan_litellm_smoke.py \
  --confirm-live \
  --report-path backend/.deer-flow/soc-internal-validation/model/litellm-smoke.json
```

报告必须为 `outcome=passed`、`passed=true`。

## 6. Start Web / 启动 Web

```bash
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon --demo-no-auth
```

Host DEV 驱动会启用隔离 SQLite、LLM analyzer、已评审 DEV Tenant Policy 和两个 SOC DEV
Workbench，关闭真实外部动作执行，并自动发现内网地址配置 Next.js/HMR。仅本机使用时加
`--local-only`。

`--demo-no-auth` 仅用于可信内网演示：页面跳过注册/登录，所有访问者共享一个合成管理员身份，
因此不能区分个人审计 actor。需要验收账号与权限时，先停止服务，再去掉该参数启动；无需改代码或数据库：

Host DEV 默认允许 3 条不同告警并行研判；同一告警的重复点击不会再次进入 Runtime/LLM。
按内网模型容量调整时，在 `.env.soc-dev.local` 设置 `SOC_LLM_MAX_CONCURRENCY`，不得取消并发上限。

```bash
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
python3.12 scripts/soc_pingan_macos_host_dev.py stop
```

## 7. Real Integration Follow-up / 真实验收顺序

```text
D12-B preflight
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
python3.12 scripts/soc_pingan_macos_host_dev.py check
python3.12 scripts/soc_pingan_macos_host_dev.py install
python3.12 scripts/soc_pingan_macos_host_dev.py start --demo-no-auth
```

`--demo-no-auth` 仅用于可信 DEV 演示；全部访问者共享一个合成管理员身份。正式身份与权限验收时去掉该参数。
告警演练默认支持 3 条不同告警并行，同一告警由服务端防重；可通过
`.env.soc-dev.local` 的 `SOC_LLM_MAX_CONCURRENCY` 调整有界容量。

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
private alert-derived data. It is intentionally separate from source control and
must remain mode `0600`, inside the approved environment only.

Extract it over the source bundle's `deer-flow-pingan-internal/` directory. Do
not commit its contents and do not copy generated batch artifacts back outside
the approved environment without review.
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
