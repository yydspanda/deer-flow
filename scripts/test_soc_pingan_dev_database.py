from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import soc_pingan_dev_database as reset


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/soc_pingan_macos_host_dev.py").write_text(
        "# host", encoding="utf-8"
    )
    database = tmp_path / reset.DEV_DATABASE
    database.parent.mkdir(parents=True)
    database.write_bytes(b"old database")
    database.with_name(database.name + "-wal").write_bytes(b"old wal")
    for name in ("soc_agent_stg.db", "deerflow.db"):
        database.with_name(name).write_bytes(b"preserve")
    return tmp_path


def invoke(root: Path, **kwargs):
    return reset.reset_dev_database(
        root=root, environment="dev", inspect_processes=lambda paths: [], **kwargs
    )


def test_preview_does_not_change_files(checkout: Path) -> None:
    before = sorted(str(path.relative_to(checkout)) for path in checkout.rglob("*"))
    report = invoke(checkout)
    assert report["status"] == "preview"
    assert report["database"] == str(checkout / reset.DEV_DATABASE)
    assert report["ready"] is True
    assert len(report["files"]) == 2
    assert (
        sorted(str(path.relative_to(checkout)) for path in checkout.rglob("*"))
        == before
    )


def test_reset_archives_only_soc_dev_family_and_restore_is_explicit(
    checkout: Path,
) -> None:
    database = checkout / reset.DEV_DATABASE
    report = invoke(checkout, confirmation="RESET-SOC-DEV")
    backup = Path(report["backup_directory"])
    assert report["status"] == "reset"
    assert not database.exists()
    assert (backup / database.name).read_bytes() == b"old database"
    assert (backup / (database.name + "-wal")).read_bytes() == b"old wal"
    assert backup.stat().st_mode & 0o777 == 0o700
    assert (backup / database.name).stat().st_mode & 0o777 == 0o600
    assert database.with_name("soc_agent_stg.db").read_bytes() == b"preserve"
    assert database.with_name("deerflow.db").read_bytes() == b"preserve"
    assert invoke(checkout, confirmation="RESET-SOC-DEV")["status"] == "already_empty"
    restore = reset.restore_dev_database(
        root=checkout,
        environment="dev",
        backup_name=backup.name,
        confirmation="RESTORE-SOC-DEV",
        inspect_processes=lambda paths: [],
    )
    assert restore["status"] == "restored"
    assert database.read_bytes() == b"old database"
    assert database.with_name(database.name + "-wal").read_bytes() == b"old wal"
    assert (backup / database.name).exists()


@pytest.mark.parametrize("environment", ["stg", "prd", "", "unknown"])
def test_no_other_environment_can_reset(checkout: Path, environment: str) -> None:
    with pytest.raises(ValueError, match="DEV only"):
        reset.reset_dev_database(
            root=checkout,
            environment=environment,
            confirmation="RESET-SOC-DEV",
            inspect_processes=lambda paths: [],
        )
    assert (checkout / reset.DEV_DATABASE).exists()


def test_busy_preview_and_apply_and_bad_confirmation(checkout: Path) -> None:
    params = dict(
        root=checkout,
        environment="dev",
        inspect_processes=lambda paths: ["TCP 8001 is listening"],
    )
    assert reset.reset_dev_database(**params)["ready"] is False
    with pytest.raises(ValueError, match="8001"):
        reset.reset_dev_database(**params, confirmation="RESET-SOC-DEV")
    with pytest.raises(ValueError, match="confirmation"):
        invoke(checkout, confirmation="yes")
    assert (checkout / reset.DEV_DATABASE).exists()


def test_symlink_and_unrecognized_checkout_refused(
    checkout: Path, tmp_path: Path
) -> None:
    database = checkout / reset.DEV_DATABASE
    database.unlink()
    target = tmp_path / "elsewhere.db"
    target.write_bytes(b"preserve")
    database.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        invoke(checkout, confirmation="RESET-SOC-DEV")
    assert target.read_bytes() == b"preserve"
    database.unlink()
    (checkout / "scripts/soc_pingan_macos_host_dev.py").unlink()
    with pytest.raises(ValueError, match="checkout"):
        invoke(checkout)


def test_move_failure_restores_originals(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = reset.os.replace
    count = 0

    def fail_second(source, target):
        nonlocal count
        if Path(source).parent == (checkout / reset.DEV_DATABASE).parent:
            count += 1
            if count == 2:
                raise OSError("simulated move failure")
        return original(source, target)

    monkeypatch.setattr(reset.os, "replace", fail_second)
    with pytest.raises(OSError, match="simulated"):
        invoke(checkout, confirmation="RESET-SOC-DEV")
    database = checkout / reset.DEV_DATABASE
    assert database.read_bytes() == b"old database"
    assert database.with_name(database.name + "-wal").read_bytes() == b"old wal"


def test_restore_rejects_new_database_and_changed_backup(checkout: Path) -> None:
    report = invoke(checkout, confirmation="RESET-SOC-DEV")
    backup = Path(report["backup_directory"])
    database = checkout / reset.DEV_DATABASE
    args = dict(
        root=checkout,
        environment="dev",
        backup_name=backup.name,
        confirmation="RESTORE-SOC-DEV",
        inspect_processes=lambda paths: [],
    )
    database.write_bytes(b"new work")
    with pytest.raises(ValueError, match="not empty"):
        reset.restore_dev_database(**args)
    assert database.read_bytes() == b"new work"
    database.unlink()
    (backup / database.name).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        reset.restore_dev_database(**args)
    assert not database.exists()


def test_restore_rejects_path_escape_and_manifest_wrong_target(checkout: Path) -> None:
    report = invoke(checkout, confirmation="RESET-SOC-DEV")
    backup = Path(report["backup_directory"])
    args = dict(
        root=checkout,
        environment="dev",
        confirmation="RESTORE-SOC-DEV",
        inspect_processes=lambda paths: [],
    )
    with pytest.raises(ValueError, match="backup name"):
        reset.restore_dev_database(**args, backup_name="../data")
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["name"] = "soc_agent_stg.db"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        reset.restore_dev_database(**args, backup_name=backup.name)


def test_interrupted_move_can_restore_remaining_files(checkout: Path) -> None:
    report = invoke(checkout, confirmation="RESET-SOC-DEV")
    backup = Path(report["backup_directory"])
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["state"] = "prepared"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    database = checkout / reset.DEV_DATABASE
    reset.os.replace(
        backup / (database.name + "-wal"), database.with_name(database.name + "-wal")
    )
    result = reset.restore_dev_database(
        root=checkout,
        environment="dev",
        backup_name=backup.name,
        confirmation="RESTORE-SOC-DEV",
        inspect_processes=lambda paths: [],
    )
    assert result["status"] == "restored"
    assert database.read_bytes() == b"old database"
    assert database.with_name(database.name + "-wal").read_bytes() == b"old wal"


def test_maintenance_lock_excludes_startup_and_second_reset(checkout: Path) -> None:
    with reset.database_maintenance_lock(checkout):
        with pytest.raises(ValueError, match="maintenance command"):
            invoke(checkout, confirmation="RESET-SOC-DEV")
    assert (checkout / reset.DEV_DATABASE).exists()


def test_process_inspection_detects_orphan_worker_and_all_bound_addresses(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = []
    monkeypatch.setattr(reset.shutil, "which", lambda name: "/usr/bin/lsof")

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "ps":
            return subprocess.CompletedProcess(
                command,
                0,
                "321 python /other/checkout/backend/scripts/soc_pingan_legacy_worker.py\n",
                "",
            )
        if "-sTCP:LISTEN" in command:
            return subprocess.CompletedProcess(command, 0, "123\n", "")
        return subprocess.CompletedProcess(command, 0, "456\n", "")

    monkeypatch.setattr(reset.subprocess, "run", run)
    blockers = reset.inspect_database_processes((checkout / reset.DEV_DATABASE,))
    assert len(blockers) == 3
    assert any("123" in item for item in blockers)
    assert any("321" in item for item in blockers)
    assert any("456" in item for item in blockers)
    assert "-iTCP:3000,8001,2026,4001,8090" in commands[0]


def test_empty_database_migrates_and_keeps_old_experience_only_in_backup(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    from soc_agent.db.migration_runner import upgrade_soc_schema

    database = checkout / reset.DEV_DATABASE
    database.unlink()
    database.with_name(database.name + "-wal").unlink()
    url = f"sqlite+pysqlite:///{database}"
    upgrade_soc_schema(url)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE old_experiment_marker (value TEXT)")
        connection.execute(
            "INSERT INTO old_experiment_marker VALUES ('preserved in backup')"
        )
    report = invoke(checkout, confirmation="RESET-SOC-DEV")
    upgrade_soc_schema(url)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT version_num FROM soc_alembic_version"
            ).fetchone()[0]
            == "0032_corpus_revision_index"
        )
        assert (
            connection.execute("SELECT count(*) FROM soc_memory_records").fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='old_experiment_marker'"
            ).fetchone()[0]
            == 0
        )
    with sqlite3.connect(
        Path(report["backup_directory"]) / database.name
    ) as connection:
        assert (
            connection.execute("SELECT value FROM old_experiment_marker").fetchone()[0]
            == "preserved in backup"
        )
