"""Offline, reversible reset of the checkout-owned SOC DEV SQLite family."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

DEV_DATABASE = Path("backend/.deer-flow/data/soc_agent_dev.db")
BACKUPS = Path("backend/.deer-flow/data/soc-dev-reset-backups")
SUFFIXES = ("", "-wal", "-shm", "-journal")
Inspector = Callable[[tuple[Path, ...]], list[str]]


def _safe_path(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        if part in ("..", "/"):
            raise ValueError("path must stay inside the checkout")
        current /= part
        if current.is_symlink():
            raise ValueError(f"refusing symlink: {current}")
    return current


def _paths(root: Path, environment: str) -> tuple[Path, ...]:
    if environment != "dev":
        raise ValueError("database reset/restore is DEV only")
    if not (root / "scripts/soc_pingan_macos_host_dev.py").is_file():
        raise ValueError("target is not a recognized Host DEV checkout")
    paths = tuple(
        _safe_path(root, Path(str(DEV_DATABASE) + suffix)) for suffix in SUFFIXES
    )
    for path in paths:
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError(
                f"refusing non-regular or hard-linked database file: {path}"
            )
    return paths


def inspect_database_processes(paths: tuple[Path, ...]) -> list[str]:
    """Require a stopped host, including orphan workers holding any SQLite file."""
    blockers: list[str] = []
    lsof = shutil.which("lsof")
    if not lsof:
        raise ValueError("lsof is required to verify the database is not open")
    listeners = subprocess.run(
        [lsof, "-nP", "-t", "-iTCP:3000,8001,2026,4001,8090", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if listeners.returncode not in (0, 1) or listeners.stderr.strip():
        raise ValueError("could not verify Host DEV listening ports with lsof")
    if listeners.stdout.strip():
        blockers.append(
            "Host DEV TCP ports are listening; PID(s): "
            + ", ".join(listeners.stdout.split())
        )
    processes = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    markers = (
        "soc_pingan_legacy_worker.py",
        "soc_pingan_legacy_api.py",
        "soc_pingan_model_gateway.py",
        "uvicorn app.gateway.app:app",
    )
    for row in processes.stdout.splitlines():
        if any(marker in row for marker in markers):
            blockers.append("Host service process still exists; PID: " + row.split()[0])
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        result = subprocess.run(
            [lsof, "-t", "--", *existing],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.stdout.strip():
            blockers.append(
                "SOC database files are open by PID(s): "
                + ", ".join(result.stdout.split())
            )
        elif result.returncode not in (0, 1) or result.stderr.strip():
            raise ValueError("could not verify open database handles with lsof")
    return blockers


@contextmanager
def database_maintenance_lock(root: Path):
    directory = _safe_path(root, DEV_DATABASE.parent)
    directory.mkdir(parents=True, exist_ok=True)
    lock = _safe_path(root, DEV_DATABASE.parent / ".soc-dev-maintenance.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another Host startup or database maintenance command is running"
            ) from exc
        yield
    finally:
        os.close(fd)


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _files(paths: tuple[Path, ...]) -> list[dict]:
    return [
        {"name": path.name, "size_bytes": path.stat().st_size, "sha256": _sha(path)}
        for path in paths
        if path.exists()
    ]


def _write_manifest(backup: Path, manifest: dict) -> None:
    staging = backup / "manifest.tmp"
    with staging.open("w", encoding="utf-8") as handle:
        staging.chmod(0o600)
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staging, backup / "manifest.json")


def reset_dev_database(
    *,
    root: Path,
    environment: str,
    confirmation: str | None = None,
    inspect_processes: Inspector = inspect_database_processes,
) -> dict:
    root = root.resolve()
    paths = _paths(root, environment)
    if confirmation not in (None, "RESET-SOC-DEV"):
        raise ValueError("confirmation must be RESET-SOC-DEV")
    blockers = inspect_processes(paths)
    report = {
        "schema_version": "soc.pingan_dev_database_reset.v1",
        "status": "preview",
        "database": str(paths[0]),
        "ready": not blockers,
        "blockers": blockers,
        "files": _files(paths),
    }
    if confirmation is None:
        return report
    with database_maintenance_lock(root):
        paths = _paths(root, environment)
        blockers = inspect_processes(paths)
        if blockers:
            raise ValueError("stop Host DEV before reset: " + "; ".join(blockers))
        files = _files(paths)
        if not files:
            return {**report, "status": "already_empty", "files": []}
        parent = _safe_path(root, BACKUPS)
        parent.mkdir(mode=0o700, exist_ok=True)
        parent.chmod(0o700)
        backup = parent / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
        )
        backup.mkdir(mode=0o700)
        manifest = {
            "schema_version": "soc.pingan_dev_database_backup.v1",
            "source_root": str(root),
            "database_relative_path": str(DEV_DATABASE),
            "state": "prepared",
            "files": files,
        }
        _write_manifest(backup, manifest)
        moved: list[tuple[Path, Path]] = []
        try:
            for path in paths:
                if not path.exists():
                    continue
                target = backup / path.name
                os.replace(path, target)
                moved.append((path, target))
                target.chmod(0o600)
            if _files(tuple(backup / item["name"] for item in files)) != files:
                raise ValueError("database changed while archiving; reset rolled back")
            _write_manifest(backup, {**manifest, "state": "complete"})
        except Exception:
            for path, target in reversed(moved):
                if path.exists():
                    raise ValueError(
                        f"concurrent database creation; preserve backup for recovery: {backup}"
                    )
                os.replace(target, path)
            raise
        return {
            **report,
            "status": "reset",
            "files": files,
            "backup_directory": str(backup),
            "next_step": "start Host DEV to initialize an empty SOC database",
        }


def restore_dev_database(
    *,
    root: Path,
    environment: str,
    backup_name: str,
    confirmation: str,
    inspect_processes: Inspector = inspect_database_processes,
) -> dict:
    root = root.resolve()
    paths = _paths(root, environment)
    if confirmation != "RESTORE-SOC-DEV":
        raise ValueError("confirmation must be RESTORE-SOC-DEV")
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{12}", backup_name):
        raise ValueError(
            "invalid backup name; use the directory name reported by reset"
        )
    backup = _safe_path(root, BACKUPS / backup_name)
    manifest_path = _safe_path(root, BACKUPS / backup_name / "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    allowed = {path.name for path in paths}
    if (
        manifest.get("schema_version") != "soc.pingan_dev_database_backup.v1"
        or manifest.get("source_root") != str(root)
        or manifest.get("database_relative_path") != str(DEV_DATABASE)
        or manifest.get("state") not in ("prepared", "complete")
        or not isinstance(files, list)
        or not files
        or len(files) > 4
        or any(
            not isinstance(item, dict) or item.get("name") not in allowed
            for item in files
        )
        or len({item["name"] for item in files}) != len(files)
    ):
        raise ValueError("invalid database backup manifest")
    with database_maintenance_lock(root):
        _paths(root, environment)
        blockers = inspect_processes(paths)
        if blockers:
            raise ValueError("stop Host DEV before restore: " + "; ".join(blockers))
        if manifest["state"] == "complete" and any(path.exists() for path in paths):
            raise ValueError(
                "SOC DEV database is not empty; reset it with its own backup before restore"
            )
        sources_list: list[Path] = []
        for item in files:
            source = _safe_path(root, BACKUPS / backup_name / item["name"])
            current = paths[0].parent / item["name"]
            if current.exists() and _files((current,)) != [item]:
                raise ValueError(
                    "SOC DEV database is not empty; current file differs from prepared backup"
                )
            sources_list.append(source if source.exists() else current)
        if any(
            path.exists() and path.name not in {item["name"] for item in files}
            for path in paths
        ):
            raise ValueError("SOC DEV database is not empty; unexpected sidecar exists")
        sources = tuple(sources_list)
        if _files(sources) != files:
            raise ValueError("backup hash/size mismatch")
        created: list[Path] = []
        try:
            for source in sources:
                target = paths[0].parent / source.name
                if target.exists():
                    continue
                with target.open("xb") as destination, source.open("rb") as original:
                    created.append(target)
                    target.chmod(0o600)
                    shutil.copyfileobj(original, destination)
                    destination.flush()
                    os.fsync(destination.fileno())
        except Exception:
            for target in created:
                target.unlink()
            raise
        return {
            "schema_version": "soc.pingan_dev_database_reset.v1",
            "status": "restored",
            "database": str(paths[0]),
            "backup_directory": str(backup),
        }
