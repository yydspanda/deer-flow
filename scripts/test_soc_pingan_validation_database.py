from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
import httpx

from scripts import soc_pingan_dev_database as database
from scripts import soc_pingan_validation_database as maintenance


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/soc_pingan_macos_host_dev.py").write_text(
        "# host", encoding="utf-8"
    )
    (tmp_path / ".env.soc-dev.local").write_text(
        "export SOC_PINGAN_ENV=dev\n", encoding="utf-8"
    )
    path = tmp_path / database.DEV_DATABASE
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE sample (batch TEXT, value TEXT)")
        conn.executemany(
            "INSERT INTO sample VALUES (?, ?)",
            [("learning", "preserved"), ("validation", "old result")],
        )
        conn.commit()
    return tmp_path


def preview(conn: sqlite3.Connection, experiment_id: str) -> dict:
    assert experiment_id == "EXP-test"
    return {
        "validation_count": conn.execute(
            "SELECT count(*) FROM sample WHERE batch='validation'"
        ).fetchone()[0]
    }


def reset(conn: sqlite3.Connection, experiment_id: str) -> dict:
    before = preview(conn, experiment_id)
    assert conn.in_transaction
    conn.execute("DELETE FROM sample WHERE batch='validation'")
    return {"deleted": before["validation_count"]}


def invoke(root: Path, **kwargs):
    return maintenance.reset_validation_database(
        root=root,
        experiment_id="EXP-test",
        inspect_processes=lambda paths: [],
        preview_store=preview,
        reset_store=reset,
        progress=lambda message: None,
        **kwargs,
    )


def rows(root: Path) -> list:
    with closing(sqlite3.connect(root / database.DEV_DATABASE)) as conn:
        return conn.execute("SELECT * FROM sample ORDER BY batch").fetchall()


@pytest.fixture
def standalone_tool(checkout: Path, monkeypatch) -> Path:
    tool_root = checkout.parent / (checkout.name + "-maintenance-tool")
    (tool_root / "backend/scripts").mkdir(parents=True)
    monkeypatch.setattr(maintenance, "ROOT", tool_root)
    old_store = checkout / "backend/scripts/soc_validation_reset_store.py"
    old_store.parent.mkdir(parents=True)
    old_store.write_text(
        "raise AssertionError('must not load the target checkout maintenance module')\n",
        encoding="utf-8",
    )
    return tool_root


def test_preview_neither_writes_nor_creates_backup(checkout: Path) -> None:
    before = {str(p): p.read_bytes() for p in checkout.rglob("*") if p.is_file()}
    report = invoke(checkout)
    assert report["status"] == "preview"
    assert report["preview"]["validation_count"] == 1
    assert before == {
        str(p): p.read_bytes() for p in checkout.rglob("*") if p.is_file()
    }
    assert not (checkout / database.BACKUPS).exists()


def test_apply_backs_up_before_deleting_and_backup_supports_existing_restore(
    checkout: Path,
) -> None:
    original = (checkout / database.DEV_DATABASE).read_bytes()
    events = []

    def reset_with_backup_check(conn, experiment_id):
        backups = list((checkout / database.BACKUPS).glob("*/manifest.json"))
        assert len(backups) == 1
        manifest = json.loads(backups[0].read_text(encoding="utf-8"))
        assert manifest["state"] == "complete"
        assert (backups[0].parent / "soc_agent_dev.db").read_bytes() == original
        return reset(conn, experiment_id)

    report = maintenance.reset_validation_database(
        root=checkout,
        experiment_id="EXP-test",
        apply=True,
        inspect_processes=lambda paths: [],
        preview_store=preview,
        reset_store=reset_with_backup_check,
        progress=events.append,
    )
    assert report["status"] == "applied"
    assert report["result"] == {"deleted": 1}
    assert rows(checkout) == [("learning", "preserved")]
    backup = Path(report["backup_directory"])
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["sha256"] == hashlib.sha256(original).hexdigest()
    assert backup.stat().st_mode & 0o777 == 0o700
    assert (backup / "soc_agent_dev.db").stat().st_mode & 0o777 == 0o600
    assert any("soc_agent_dev.db" in event for event in events)
    # Existing restore stays explicit and never overwrites the active database.
    with pytest.raises(ValueError, match="not empty"):
        database.restore_dev_database(
            root=checkout,
            environment="dev",
            backup_name=backup.name,
            confirmation="RESTORE-SOC-DEV",
            inspect_processes=lambda paths: [],
        )
    (checkout / database.DEV_DATABASE).unlink()
    database.restore_dev_database(
        root=checkout,
        environment="dev",
        backup_name=backup.name,
        confirmation="RESTORE-SOC-DEV",
        inspect_processes=lambda paths: [],
    )
    assert (checkout / database.DEV_DATABASE).read_bytes() == original


def test_sql_failure_rolls_back_and_keeps_verified_backup(checkout: Path) -> None:
    def fail(conn, experiment_id):
        reset(conn, experiment_id)
        raise ValueError("protection invariant failed")

    with pytest.raises(ValueError, match="protection invariant"):
        maintenance.reset_validation_database(
            root=checkout,
            experiment_id="EXP-test",
            apply=True,
            inspect_processes=lambda paths: [],
            preview_store=preview,
            reset_store=fail,
            progress=lambda message: None,
        )
    assert len(rows(checkout)) == 2
    manifests = list((checkout / database.BACKUPS).glob("*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text(encoding="utf-8"))["state"] == "complete"


@pytest.mark.parametrize("apply", [False, True])
def test_live_services_are_refused(checkout: Path, apply: bool) -> None:
    with pytest.raises(ValueError, match="8001"):
        maintenance.reset_validation_database(
            root=checkout,
            experiment_id="EXP-test",
            apply=apply,
            inspect_processes=lambda paths: ["TCP 8001 is listening"],
            preview_store=preview,
            reset_store=reset,
        )
    assert len(rows(checkout)) == 2
    assert not (checkout / database.BACKUPS).exists()


def test_symlink_database_is_refused(checkout: Path) -> None:
    path = checkout / database.DEV_DATABASE
    target = path.with_name("elsewhere.db")
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        invoke(checkout, apply=True)
    assert not (checkout / database.BACKUPS).exists()


def test_actual_stg_profile_overrides_exported_dev(checkout: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOC_PINGAN_ENV", "dev")
    (checkout / ".env.soc-dev.local").write_text(
        "export SOC_PINGAN_ENV=stg\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="DEV only"):
        invoke(checkout, apply=True)
    assert len(rows(checkout)) == 2


def test_startup_maintenance_lock_blocks_apply(checkout: Path) -> None:
    with database.database_maintenance_lock(checkout):
        with pytest.raises(ValueError, match="maintenance command"):
            invoke(checkout, apply=True)
    assert len(rows(checkout)) == 2


def test_cli_defaults_to_preview_and_loads_store_from_standalone_tool(
    checkout: Path, standalone_tool: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(database, "inspect_database_processes", lambda paths: [])
    store = standalone_tool / "backend/scripts/soc_validation_reset_store.py"
    store.write_text(
        "def preview(conn, experiment_id):\n"
        "    return {'experiment_id': experiment_id}\n"
        "def reset(conn, experiment_id):\n"
        "    raise AssertionError('must not reset')\n",
        encoding="utf-8",
    )
    assert maintenance.main(["--root", str(checkout), "--experiment", "EXP-test"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "preview"
    assert report["database"] == str(checkout / database.DEV_DATABASE)
    assert report["preview"]["experiment_id"] == "EXP-test"
    assert not (checkout / database.BACKUPS).exists()


@pytest.mark.parametrize("apply", [False, True])
def test_cli_forwards_explicit_learning_round_to_each_store_phase(
    checkout: Path, standalone_tool: Path, capsys, monkeypatch, apply: bool
) -> None:
    monkeypatch.setattr(database, "inspect_database_processes", lambda paths: [])
    store = standalone_tool / "backend/scripts/soc_validation_reset_store.py"
    store.write_text(
        "def preview(conn, experiment_id, *, learning_round_id):\n"
        "    assert experiment_id == 'EXP-test'\n"
        "    assert learning_round_id == 'ROUND-selected'\n"
        "    assert conn.execute('PRAGMA query_only').fetchone()[0] == 1\n"
        "    return {'learning_round_id': learning_round_id}\n"
        "def reset(conn, experiment_id, *, learning_round_id):\n"
        "    assert experiment_id == 'EXP-test'\n"
        "    assert learning_round_id == 'ROUND-selected'\n"
        "    assert conn.in_transaction\n"
        "    conn.execute(\"DELETE FROM sample WHERE batch='validation'\")\n"
        "    return {'learning_round_id': learning_round_id}\n",
        encoding="utf-8",
    )
    args = [
        "--root",
        str(checkout),
        "--experiment",
        "EXP-test",
        "--learning-round",
        "ROUND-selected",
    ]
    if apply:
        args.append("--apply")
    assert maintenance.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["learning_round_id"] == "ROUND-selected"
    assert report["preview"]["learning_round_id"] == "ROUND-selected"
    if apply:
        assert report["result"]["learning_round_id"] == "ROUND-selected"
        assert Path(report["backup_directory"], "manifest.json").is_file()
        assert rows(checkout) == [("learning", "preserved")]
    else:
        assert not (checkout / database.BACKUPS).exists()
        assert len(rows(checkout)) == 2


def test_all_sqlite_family_files_are_backed_up(checkout: Path) -> None:
    paths = database._paths(checkout, "dev")
    for path in paths[1:]:
        path.write_bytes(path.name.encode())
    backup = maintenance._backup(checkout, paths, "EXP-test", lambda message: None)
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert {item["name"] for item in manifest["files"]} == {path.name for path in paths}
    for path in paths:
        assert (backup / path.name).read_bytes() == path.read_bytes()


def test_backup_verification_failure_never_starts_cleanup(
    checkout: Path, monkeypatch
) -> None:
    monkeypatch.setattr(database, "_files", lambda paths: [])
    with pytest.raises(ValueError, match="verification failed"):
        invoke(checkout, apply=True)
    assert len(rows(checkout)) == 2
    manifests = list((checkout / database.BACKUPS).glob("*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text(encoding="utf-8"))["state"] == "prepared"


@pytest.fixture
def real_store_checkout(checkout: Path, standalone_tool: Path, monkeypatch) -> Path:
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo / "backend"))
    spec = importlib.util.spec_from_file_location(
        "_validation_reset_fixture",
        repo / "backend/tests/test_soc_validation_reset_store.py",
    )
    assert spec and spec.loader
    fixtures = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixtures)
    spec.loader.exec_module(fixtures)
    seed_dir = checkout / "fixture-source"
    seed_dir.mkdir()
    seeded = fixtures.database.__wrapped__(seed_dir)
    original = next(seeded)
    path = checkout / database.DEV_DATABASE
    try:
        with closing(sqlite3.connect(path)) as destination:
            original.backup(destination)
    finally:
        seeded.close()
        original.close()
    target_store = standalone_tool / "backend/scripts/soc_validation_reset_store.py"
    shutil.copyfile(
        repo / "backend/scripts/soc_validation_reset_store.py", target_store
    )
    return checkout


def test_real_sql_store_cleanup_and_full_backup_restore(
    real_store_checkout: Path,
) -> None:
    checkout = real_store_checkout
    path = checkout / database.DEV_DATABASE
    before = path.read_bytes()
    with closing(sqlite3.connect(path)) as conn:
        learning = conn.execute(
            "SELECT * FROM soc_analysis_runs WHERE alert_id='L'"
        ).fetchall()
        memory = conn.execute("SELECT * FROM soc_memory_records").fetchall()
        members = conn.execute("SELECT * FROM soc_corpus_experiment_members").fetchall()
    params = {
        "root": checkout,
        "experiment_id": "EXP-test",
        "inspect_processes": lambda paths: [],
        "progress": lambda message: None,
    }
    report = maintenance.reset_validation_database(**params)
    assert report["preview"]["runs"] == 2
    assert report["preview"]["jobs"] == 3
    assert (
        report["preview"]["first_batch_options"]["normalization_review_mode"] == "apply"
    )
    assert path.read_bytes() == before
    assert not (checkout / database.BACKUPS).exists()
    result = maintenance.reset_validation_database(**params, apply=True)
    assert result["status"] == "applied"
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT alert_id FROM soc_analysis_runs").fetchall() == [
            ("L",)
        ]
        assert conn.execute("SELECT alert_id FROM soc_processing_jobs").fetchall() == [
            ("L",)
        ]
        assert conn.execute("SELECT batch FROM soc_corpus_rounds").fetchall() == [
            ("learning",)
        ]
        assert (
            conn.execute(
                "SELECT * FROM soc_analysis_runs WHERE alert_id='L'"
            ).fetchall()
            == learning
        )
        assert conn.execute("SELECT * FROM soc_memory_records").fetchall() == memory
        assert (
            conn.execute("SELECT * FROM soc_corpus_experiment_members").fetchall()
            == members
        )
    backup = Path(result["backup_directory"])
    assert (backup / path.name).read_bytes() == before
    # Preserve the cleaned family before exercising the existing explicit restore.
    cleared = path.with_name("cleared-validation.db")
    path.rename(cleared)
    database.restore_dev_database(
        root=checkout,
        environment="dev",
        backup_name=backup.name,
        confirmation="RESTORE-SOC-DEV",
        inspect_processes=lambda paths: [],
    )
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM soc_analysis_runs").fetchone()[0] == 3
        assert (
            conn.execute("SELECT count(*) FROM soc_processing_jobs").fetchone()[0] == 4
        )
        assert conn.execute("SELECT * FROM soc_memory_records").fetchall() == memory


@pytest.fixture
def mixed_learning_checkout(real_store_checkout: Path) -> Path:
    path = real_store_checkout / database.DEV_DATABASE
    with closing(sqlite3.connect(path)) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(soc_corpus_rounds)")]
        row = list(
            conn.execute(
                "SELECT * FROM soc_corpus_rounds WHERE round_id='ROUND-learning'"
            ).fetchone()
        )
        row[columns.index("round_id")] = "ROUND-old-off"
        payload_index = columns.index("record_payload")
        payload = json.loads(row[payload_index])
        payload["options"]["normalization_review_mode"] = "off"
        row[payload_index] = json.dumps(payload)
        conn.execute(
            f"INSERT INTO soc_corpus_rounds VALUES ({','.join('?' for _ in row)})",
            row,
        )
        conn.commit()
    return real_store_checkout


def test_explicit_baseline_cleans_mixed_history_and_preserves_learning(
    mixed_learning_checkout: Path,
) -> None:
    checkout = mixed_learning_checkout
    path = checkout / database.DEV_DATABASE
    protected_queries = {
        "rounds": "SELECT * FROM soc_corpus_rounds WHERE batch='learning' ORDER BY round_id",
        "items": "SELECT * FROM soc_corpus_round_items WHERE round_id='ROUND-learning'",
        "jobs": "SELECT * FROM soc_processing_jobs WHERE alert_id='L'",
        "runs": "SELECT * FROM soc_analysis_runs WHERE alert_id='L'",
        "memory": "SELECT * FROM soc_memory_records",
        "members": "SELECT * FROM soc_corpus_experiment_members",
    }
    with closing(sqlite3.connect(path)) as conn:
        protected = {
            key: conn.execute(query).fetchall()
            for key, query in protected_queries.items()
        }
    before = path.read_bytes()
    params = {
        "root": checkout,
        "experiment_id": "EXP-test",
        "learning_round_id": "ROUND-learning",
        "inspect_processes": lambda paths: [],
        "progress": lambda message: None,
    }
    report = maintenance.reset_validation_database(**params)
    assert (
        report["preview"]["first_batch_options"]["normalization_review_mode"] == "apply"
    )
    assert report["learning_round_id"] == "ROUND-learning"
    assert path.read_bytes() == before
    assert not (checkout / database.BACKUPS).exists()
    report = maintenance.reset_validation_database(**params, apply=True)
    assert report["status"] == "applied"
    assert (
        report["result"]["first_batch_options"]["normalization_review_mode"] == "apply"
    )
    assert Path(report["backup_directory"], path.name).read_bytes() == before
    with closing(sqlite3.connect(path)) as conn:
        assert {
            key: conn.execute(query).fetchall()
            for key, query in protected_queries.items()
        } == protected
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM soc_corpus_rounds WHERE batch='validation'"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT alert_id FROM soc_processing_jobs").fetchall() == [
            ("L",)
        ]


def test_shared_normalization_issue_preserves_history_and_restores_original_link(
    mixed_learning_checkout: Path,
) -> None:
    from soc_agent.contracts.schemas import NormalizationMaintenanceIssue

    checkout = mixed_learning_checkout
    path = checkout / database.DEV_DATABASE
    table = "soc_normalization_maintenance_issues"
    protected_queries = {
        "rounds": "SELECT * FROM soc_corpus_rounds WHERE batch='learning' ORDER BY round_id",
        "items": "SELECT * FROM soc_corpus_round_items WHERE round_id='ROUND-learning'",
        "jobs": "SELECT * FROM soc_processing_jobs WHERE alert_id='L'",
        "runs": "SELECT * FROM soc_analysis_runs WHERE alert_id='L'",
        "memory": "SELECT * FROM soc_memory_records",
        "members": "SELECT * FROM soc_corpus_experiment_members",
    }
    with closing(sqlite3.connect(path)) as conn:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        for issue_id, alert, count in (
            ("NMI-shared", "V", 4104),
            ("NMI-learning", "L", 7),
        ):
            payload = NormalizationMaintenanceIssue(
                issue_id=issue_id,
                dedupe_key="parser-schema:" + issue_id,
                issue_type="novel_schema",
                severity="warning",
                status="resolved",
                tenant_id="pingan",
                source_system="test-source",
                adapter="test-adapter",
                parser_name="test-parser",
                parser_version="v1",
                schema_fingerprint="shared-schema",
                source_path="$.event.source",
                expected_target="source_ip",
                run_id="RUN-" + alert * 12,
                alert_id=alert,
                occurrence_count=count,
                first_seen_at="2026-09-01T01:02:03Z",
                last_seen_at="2026-09-22T04:05:06Z",
                acknowledged_by={"actor_id": "reviewer-a"},
                acknowledged_at="2026-09-10T01:02:03Z",
                resolved_by={"actor_id": "reviewer-b"},
                resolved_at="2026-09-20T01:02:03Z",
                resolution_reason="人工确认保留历史",
                details={
                    "observed_paths": ["$.event.source"],
                    "review_note": "共享解析问题",
                },
            ).model_dump(mode="json")
            record = {
                **payload,
                "acknowledged_by_actor_id": "reviewer-a",
                "resolved_by_actor_id": "reviewer-b",
                "issue_payload": json.dumps(payload, ensure_ascii=False),
            }
            conn.execute(
                f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
                [record[column] for column in columns],
            )
        conn.commit()
        protected = {
            key: conn.execute(query).fetchall()
            for key, query in protected_queries.items()
        }
        original_issues = conn.execute(
            f"SELECT * FROM {table} ORDER BY issue_id"
        ).fetchall()
    before = path.read_bytes()
    store = maintenance._load_store()

    def reset_after_backup(conn, experiment_id, *, learning_round_id):
        manifests = list((checkout / database.BACKUPS).glob("*/manifest.json"))
        assert len(manifests) == 1
        assert (
            json.loads(manifests[0].read_text(encoding="utf-8"))["state"] == "complete"
        )
        backup_path = manifests[0].parent / path.name
        assert backup_path.read_bytes() == before
        with closing(sqlite3.connect(backup_path)) as backup_conn:
            assert (
                backup_conn.execute(
                    f"SELECT * FROM {table} ORDER BY issue_id"
                ).fetchall()
                == original_issues
            )
        assert [
            tuple(row)
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY issue_id")
        ] == original_issues
        return store.reset(conn, experiment_id, learning_round_id=learning_round_id)

    params = {
        "root": checkout,
        "experiment_id": "EXP-test",
        "learning_round_id": "ROUND-learning",
        "inspect_processes": lambda paths: [],
        "preview_store": store.preview,
        "reset_store": reset_after_backup,
        "progress": lambda message: None,
    }
    assert maintenance.reset_validation_database(**params)["status"] == "preview"
    assert path.read_bytes() == before
    assert not (checkout / database.BACKUPS).exists()
    report = maintenance.reset_validation_database(**params, apply=True)
    assert report["status"] == "applied"
    with closing(sqlite3.connect(path)) as conn:
        issues = conn.execute(f"SELECT * FROM {table} ORDER BY issue_id").fetchall()
        assert len(issues) == len(original_issues) == 2
        assert (
            issues[0] == original_issues[0]
        )  # The learning issue remains byte-for-byte.
        expected_shared = dict(zip(columns, original_issues[1], strict=True))
        expected_payload = json.loads(expected_shared.pop("issue_payload"))
        expected_shared["run_id"] = None
        expected_payload["run_id"] = None
        actual_shared = dict(zip(columns, issues[1], strict=True))
        actual_payload = json.loads(actual_shared.pop("issue_payload"))
        assert actual_shared == expected_shared
        assert actual_payload == expected_payload
        assert (
            NormalizationMaintenanceIssue.model_validate(actual_payload).run_id is None
        )
        assert {
            key: conn.execute(query).fetchall()
            for key, query in protected_queries.items()
        } == protected
        assert conn.execute("SELECT alert_id FROM soc_analysis_runs").fetchall() == [
            ("L",)
        ]
    path.rename(path.with_name("cleared-validation.db"))
    database.restore_dev_database(
        root=checkout,
        environment="dev",
        backup_name=Path(report["backup_directory"]).name,
        confirmation="RESTORE-SOC-DEV",
        inspect_processes=lambda paths: [],
    )
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as conn:
        assert (
            conn.execute(f"SELECT * FROM {table} ORDER BY issue_id").fetchall()
            == original_issues
        )


@pytest.mark.parametrize("baseline", [None, "ROUND-missing", "ROUND-validation"])
@pytest.mark.parametrize("apply", [False, True])
def test_mixed_history_requires_valid_explicit_baseline_before_backup(
    mixed_learning_checkout: Path,
    baseline: str | None,
    apply: bool,
) -> None:
    checkout = mixed_learning_checkout
    path = checkout / database.DEV_DATABASE
    before = path.read_bytes()
    with pytest.raises(ValueError):
        maintenance.reset_validation_database(
            root=checkout,
            experiment_id="EXP-test",
            learning_round_id=baseline,
            apply=apply,
            inspect_processes=lambda paths: [],
            progress=lambda message: None,
        )
    assert path.read_bytes() == before
    assert not (checkout / database.BACKUPS).exists()


def _runbook_start_code() -> str:
    path = Path(__file__).resolve().parents[1] / "docs/soc-validation-reset.md"
    section = path.read_text(encoding="utf-8").split("## 5.", 1)[1]
    matches = re.findall(r"<<'PY'\n(.*?)\nPY\n", section, re.DOTALL)
    assert len(matches) == 1
    return matches[0]


def _start_receipt(
    tmp_path: Path, *, status: str = "applied", policy: bool = True
) -> dict:
    receipt = {
        "status": status,
        "experiment_id": "EXP-test",
        "backup_directory": str(tmp_path / "verified-backup"),
        "result": {
            "first_batch_options": {
                "normalization_review_mode": "shadow",
                "refresh_normalization": True,
                "tenant_policy_enabled": policy,
                "tenant_policy_advisor_enabled": False,
                "tenant_policy_signal_providers_enabled": policy,
            },
        },
    }
    path = tmp_path / "backend/.deer-flow/soc-internal-validation/validation-reset.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt


@pytest.mark.parametrize("policy", [False, True])
def test_runbook_start_submits_exact_saved_options_and_reuses_request_key(
    tmp_path: Path,
    monkeypatch,
    policy: bool,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand

    receipt = _start_receipt(tmp_path, policy=policy)
    monkeypatch.chdir(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 2026
        if request.method == "GET":
            assert request.url.path.endswith("/experiments/configuration")
            assert request.url.params["batch"] == "validation"
            return httpx.Response(200, json={"max_concurrency": 4})
        if request.url.path.endswith("/rounds"):
            body = json.loads(request.content)
            command = CorpusRoundCreateCommand.model_validate(body)
            assert (
                command.options.model_dump() == receipt["result"]["first_batch_options"]
            )
            assert command.selection.model_dump() == {
                "batch": "validation",
                "scope": "all",
                "group_ids": [],
                "rule_codes": [],
                "alert_ids": [],
            }
            assert command.experiment_id == "EXP-test"
            assert command.purpose == ("full_flow" if policy else "memory")
            assert command.memory_mode == "snapshot"
            assert command.execution_limit == 2147483647
            assert command.concurrency == 4
            assert request.headers["Content-Type"] == "application/json"
            return httpx.Response(201, json={"round_id": "ROUND-new"})
        assert request.url.path.endswith("/rounds/ROUND-new/start")
        assert json.loads(request.content) == {}
        return httpx.Response(200, json={"state": "running"})

    client = httpx.Client

    def mock_client(**kwargs):
        assert kwargs == {"timeout": 180, "trust_env": False}
        return client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", mock_client)
    code = _runbook_start_code()
    for _ in range(2):
        exec(compile(code, "docs/soc-validation-reset.md", "exec"), {})
    assert len(requests) == 6
    creates = [request for request in requests if request.url.path.endswith("/rounds")]
    key = (
        "validation-reset-"
        + hashlib.sha256(receipt["backup_directory"].encode()).hexdigest()
    )
    assert [request.headers["Idempotency-Key"] for request in creates] == [key, key]
    assert creates[0].content == creates[1].content


@pytest.mark.parametrize("failure", ["not_applied", "creation_failed"])
def test_runbook_never_starts_without_successful_cleanup_and_creation(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    _start_receipt(
        tmp_path, status="preview" if failure == "not_applied" else "applied"
    )
    monkeypatch.chdir(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        assert not request.url.path.endswith("/start")
        if request.method == "GET":
            return httpx.Response(200, json={"max_concurrency": 4})
        return httpx.Response(409, json={"detail": "creation rejected"})

    client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(SystemExit, match="没有成功清理记录|创建失败 HTTP 409"):
        exec(compile(_runbook_start_code(), "docs/soc-validation-reset.md", "exec"), {})
    assert len(requests) == (0 if failure == "not_applied" else 2)
