#!/usr/bin/env python3
"""Preview or clear one stopped Host DEV experiment's second-batch results."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

if __package__:
    from . import soc_pingan_dev_database as database
else:
    import soc_pingan_dev_database as database

ROOT = Path(__file__).resolve().parents[1]
StoreOperation = Callable[[sqlite3.Connection, str], dict]


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _runtime_environment(root: Path) -> str:
    """Read the same trusted overlay as Host startup without exposing its values."""
    profile = database._safe_path(root, Path(".env.soc-dev.local"))
    if not profile.is_file():
        return os.environ.get("SOC_PINGAN_ENV", "").strip().lower()
    environment = {
        **os.environ,
        "SOC_HOST_DEV_ROOT": str(root),
        "SOC_REPO_ROOT": str(root),
        "DEER_FLOW_CONFIG_PATH": str(root / "config.pingan-dev.local"),
    }
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1" >/dev/null || exit; printf "%s" "${SOC_PINGAN_ENV:-}"',
            "soc-validation-environment",
            str(profile),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise ValueError("could not read the Host runtime profile")
    return result.stdout.strip().lower()


def _load_store(root: Path):
    path = database._safe_path(
        root, Path("backend/scripts/soc_validation_reset_store.py")
    )
    if not path.is_file():
        raise ValueError(f"required validation maintenance module is missing: {path}")
    spec = importlib.util.spec_from_file_location(
        "_soc_validation_reset_store_" + uuid4().hex, path
    )
    if spec is None or spec.loader is None:
        raise ValueError("could not load the validation maintenance module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_stopped(paths: tuple[Path, ...], inspector: database.Inspector) -> None:
    blockers = inspector(paths)
    if blockers:
        raise ValueError(
            "stop Host DEV before validation cleanup: " + "; ".join(blockers)
        )


def _backup(
    root: Path,
    paths: tuple[Path, ...],
    experiment_id: str,
    progress: Callable[[str], None],
) -> Path:
    existing = tuple(path for path in paths if path.exists())
    parent = database._safe_path(root, database.BACKUPS)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    if shutil.disk_usage(parent).free < sum(path.stat().st_size for path in existing):
        raise ValueError("insufficient disk space for the full SOC database backup")
    backup = parent / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12])
    backup.mkdir(mode=0o700)
    manifest = {
        "schema_version": "soc.pingan_dev_database_backup.v1",
        "source_root": str(root),
        "database_relative_path": str(database.DEV_DATABASE),
        "state": "prepared",
        "files": [],
        "purpose": "validation_batch_cleanup",
        "experiment_id": experiment_id,
    }
    database._write_manifest(backup, manifest)
    progress(f"备份目录：{backup}")
    files = []
    for source in existing:
        before = source.stat()
        copied, last_report = 0, time.monotonic()
        digest = hashlib.sha256()
        target = backup / source.name
        progress(f"正在复制 {source.name}（{before.st_size / 1024**3:.2f} GiB）…")
        with source.open("rb") as original, target.open("xb") as destination:
            target.chmod(0o600)
            while chunk := original.read(8 * 1024 * 1024):
                destination.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
                if time.monotonic() - last_report >= 10:
                    progress(f"{source.name} 已复制 {copied / 1024**3:.2f} GiB")
                    last_report = time.monotonic()
            destination.flush()
            os.fsync(destination.fileno())
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError(
                f"database changed during backup; original data was not cleared: {backup}"
            )
        item = {"name": source.name, "size_bytes": copied, "sha256": digest.hexdigest()}
        progress(f"正在校验 {source.name} 的备份 SHA256…")
        if database._files((target,)) != [item]:
            raise ValueError(
                f"backup verification failed; original data was not cleared: {backup}"
            )
        files.append(item)
    if tuple(path for path in paths if path.exists()) != existing:
        raise ValueError(
            "database sidecars changed during backup; cleanup was not started"
        )
    database._write_manifest(backup, {**manifest, "files": files, "state": "complete"})
    progress("完整数据库备份及校验已完成，准备清理第二批记录。")
    return backup


def _preview(path: Path, experiment_id: str, operation: StoreOperation) -> dict:
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return operation(conn, experiment_id)


def reset_validation_database(
    *,
    root: Path,
    experiment_id: str,
    apply: bool = False,
    environment: str | None = None,
    inspect_processes: database.Inspector | None = None,
    preview_store: StoreOperation | None = None,
    reset_store: StoreOperation | None = None,
    progress: Callable[[str], None] = _progress,
) -> dict:
    root = root.expanduser().absolute()
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("refusing symlink checkout path")
    paths = database._paths(
        root, environment if environment is not None else _runtime_environment(root)
    )
    if not paths[0].is_file():
        raise ValueError("SOC DEV database does not exist; no database was created")
    if not experiment_id.strip():
        raise ValueError("experiment ID is required")
    inspector = inspect_processes or database.inspect_database_processes
    if preview_store is None or reset_store is None:
        store = _load_store(root)
        preview_store, reset_store = store.preview, store.reset
    report = {
        "schema_version": "soc.pingan_validation_database_reset.v1",
        "status": "preview",
        "database": str(paths[0]),
        "experiment_id": experiment_id,
    }
    if not apply:
        _require_stopped(paths, inspector)
        progress("正在只读检查第二批归属及历史引用；大数据库可能需要等待几分钟…")
        return {**report, "preview": _preview(paths[0], experiment_id, preview_store)}
    with database.database_maintenance_lock(root):
        paths = database._paths(root, "dev")
        _require_stopped(paths, inspector)
        progress("正在检查第二批归属及历史引用；检查通过后才开始完整备份…")
        details = _preview(paths[0], experiment_id, preview_store)
        backup = _backup(root, paths, experiment_id, progress)
        _require_stopped(paths, inspector)
        with closing(
            sqlite3.connect(
                paths[0].as_uri() + "?mode=rw",
                uri=True,
                timeout=3,
                isolation_level=None,
            )
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                conn.execute("BEGIN IMMEDIATE")
                progress(
                    "正在事务内复验历史引用并清理第二批；大数据库可能需要等待几分钟…"
                )
                result = reset_store(conn, experiment_id)
                conn.commit()
            except BaseException:
                conn.rollback()
                progress(f"清理未完成，事务已回滚；完整备份保留于：{backup}")
                raise
        return {
            **report,
            "status": "applied",
            "preview": details,
            "result": result,
            "backup_directory": str(backup),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--experiment", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="back up then clear this experiment's validation results",
    )
    args = parser.parse_args(argv)
    try:
        report = reset_validation_database(
            root=args.root, experiment_id=args.experiment, apply=args.apply
        )
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"Validation cleanup stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
