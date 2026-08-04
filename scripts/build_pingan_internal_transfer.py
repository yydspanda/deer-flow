#!/usr/bin/env python3
"""Build separate source and private-overlay archives for PingAn internal DEV."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "backend/.deer-flow/internal-transfer"
ARCHIVE_ROOT = "deer-flow-pingan-internal"
MANIFEST_SCHEMA_VERSION = "soc.pingan_internal_transfer_manifest.v1"

SOURCE_EXCLUDED_PREFIXES = (
    ".codegraph/",
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
SOURCE_FORBIDDEN_SUFFIXES = frozenset({".db", ".key", ".pem", ".pkl", ".sqlite", ".sqlite3", ".xlsx"})
PRIVATE_OVERLAY_PATHS = (
    ".env.soc-dev.local",
    "config.pingan-dev.local",
    "validation/original_works/raw_program/Deepseek_Qwen_32B_EDR_Analysis_Ignored_Paths_Sup (1).xlsx",
    "datas/source/full_alert_2026_month_forth_sample_200.pkl",
    "backend/.deer-flow/pingan-context/software-path-catalog.sqlite",
    "backend/.deer-flow/pingan-context/software-path-catalog.build-report.json",
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
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    source_paths = collect_source_paths(root)
    git_info = _git_info(root)
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
        readme=_source_readme(timestamp),
    )
    archives: dict[str, Any] = {
        "source": _archive_result(source_path, source_manifest),
    }

    if include_private_overlay:
        private_paths = [Path(item) for item in PRIVATE_OVERLAY_PATHS]
        missing = [path.as_posix() for path in private_paths if not (root / path).is_file()]
        if missing:
            raise ValueError(f"private overlay inputs are missing: {missing}")
        private_manifest = _archive_manifest(
            archive_kind="private_overlay",
            root=root,
            paths=private_paths,
            created_at=timestamp,
            git_info=git_info,
            private=True,
        )
        private_path = output_dir / f"deer-flow-pingan-private-overlay-{timestamp}.tar.gz"
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

    report = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "output_directory": str(output_dir),
        "archives": archives,
        "secrets_in_console_output": False,
    }
    report_path = output_dir / f"transfer-report-{timestamp}.json"
    _write_private_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def inspect_archive(path: Path) -> dict[str, Any]:
    """Verify archive path safety, manifest presence, and per-file digests."""

    archive = path.expanduser().resolve()
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        _assert_safe_members(members)
        manifest_members = [member for member in members if member.name.startswith(f"{ARCHIVE_ROOT}/TRANSFER-MANIFEST.") and member.name.endswith(".json")]
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
                raise ValueError(f"archive manifest contains a duplicate path: {relative}")
            if archive_kind == "source":
                _assert_source_path_safe(Path(relative))
            expected[relative] = item
        if manifest.get("file_count") != len(expected):
            raise ValueError("archive manifest file count is invalid")
        metadata_names = {
            manifest_member.name,
            f"{ARCHIVE_ROOT}/INTERNAL-BUNDLE-README.{archive_kind}.md",
        }
        expected_names = metadata_names | {f"{ARCHIVE_ROOT}/{relative}" for relative in expected}
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
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
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


def _source_path_excluded(relative: str) -> bool:
    return any(relative.startswith(prefix) for prefix in SOURCE_EXCLUDED_PREFIXES)


def _assert_source_path_safe(relative: Path) -> None:
    if relative.name in SOURCE_FORBIDDEN_NAMES:
        raise ValueError(f"forbidden local configuration entered source archive: {relative}")
    if relative.suffix.lower() in SOURCE_FORBIDDEN_SUFFIXES:
        raise ValueError(f"forbidden private data entered source archive: {relative}")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe source archive path: {relative}")


def _assert_safe_members(members: Iterable[tarfile.TarInfo]) -> None:
    prefix = PurePosixPath(ARCHIVE_ROOT)
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] != prefix.parts:
            raise ValueError(f"unsafe archive member: {member.name}")
        if member.isdev() or member.isfifo():
            raise ValueError(f"unsupported archive member: {member.name}")
        if member.islnk():
            raise ValueError(f"hard links are unsupported in transfer archives: {member.name}")
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
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
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


def _source_readme(timestamp: str) -> str:
    return f"""# PingAn Internal Source Bundle

Built at `{timestamp}` from the current working tree, including non-ignored
uncommitted source files. It excludes credentials, private PKL/XLSX, generated
SQLite, virtual environments, Node dependencies, Git metadata, static knowledge
snapshots, archived notes, and large demo media.

Extract this archive first. Then extract the separately protected private-overlay
archive into the same parent directory. Read:

- `.notes/ai_soc/integrations/pingan-internal-continuation-handoff.md`
- `backend/samples/pingan_dev/README.md`
- `validation/compact_zeus/internal_batch/README.md`

Install backend dependencies with:

```bash
cd backend
uv sync --locked --extra pingan-dev
```
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
    parser.add_argument("--inspect", type=Path, help="Inspect one existing archive instead of building")
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
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, subprocess.CalledProcessError, tarfile.TarError, ValueError) as exc:
        print(f"error: {str(exc)[:1000] or type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
