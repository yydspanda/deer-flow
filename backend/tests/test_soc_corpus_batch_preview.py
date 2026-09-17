import hashlib
import json
import runpy
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from soc_agent.demo.corpus_batch_preview import prepare_batch_preview
from soc_agent.memory.profiles import SocMemoryProfileIdentity

PROFILE = {"profile_id": "test", "profile_version": "1", "feature_schema_version": "test.v1", "aggregation_window_seconds": 2592000}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source(tmp_path):
    # Arbitrary bytes ensure preparation never unpickles or runs an analyzer.
    pkl = tmp_path / "sample.pkl"
    pkl.write_bytes(b"not executable pickle data")
    store = tmp_path / "sample.workbench-payloads.sqlite"
    rows = [
        {
            "alert_id": str(i),
            "source_index": i,
            "payload_hash": f"{i:064x}",
            "observed_at": f"2026-08-01T0{i}:00:00+00:00",
            "group_id": "group",
            "window_id": "window",
            "behavior_fingerprint": "fp",
            "decision_eligible": True,
            "rule_name": "Example rule",
            "operational_label": "must not appear in manifest",
            "operational_label_reason": "private label",
        }
        for i in range(6)
    ]
    with sqlite3.connect(store) as connection:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", [("schema_version", "soc.corpus_workbench_payload_store.v1"), ("source_sha256", digest(pkl)), ("alert_count", "6")])
        connection.execute("CREATE TABLE payloads(alert_id TEXT, source_index INTEGER, payload_hash TEXT)")
        connection.executemany("INSERT INTO payloads VALUES (?, ?, ?)", [(r["alert_id"], r["source_index"], r["payload_hash"]) for r in rows])
    index = tmp_path / "sample.workbench-index.json"
    document = {
        "schema_version": "soc.corpus_workbench_index.v3",
        "source": {"file_name": pkl.name, "sha256": digest(pkl), "size_bytes": pkl.stat().st_size, "alert_count": 6},
        "payload_store": {"schema_version": "soc.corpus_workbench_payload_store.v1", "file_name": store.name, "sha256": digest(store), "size_bytes": store.stat().st_size},
        "memory_profile": PROFILE,
        "cases": rows,
    }
    index.write_text(json.dumps(document), encoding="utf-8")
    return pkl, store, index, document


def prepare(source, target):
    pkl, _, index, _ = source
    return prepare_batch_preview(source_path=pkl, index_path=index, output_dir=target, expected_profile=PROFILE)


def test_preview_verifies_all_sources_without_mutation_or_label_export(source, tmp_path):
    originals = {p: digest(p) for p in source[:3]}
    output = tmp_path / "preview"
    result = prepare(source, output)
    assert result.counts == {"learning": 5, "validation_main": 1, "validation_supplementary": 0, "total": 6}
    assert {p: digest(p) for p in source[:3]} == originals
    assert not source[1].with_name(source[1].name + "-wal").exists()
    document = json.loads((output / "manifest.json").read_text())
    assert document["approval_status"] == "draft"
    assert document["execution_enabled"] is False
    assert document["max_learning_per_group"] == 10
    assert document["learning_aggregation"] == "experiment_actual_pattern"
    assert document["learning_aggregation_status"] == "planned"
    assert document["source_identity"]["memory_profile"] == PROFILE
    assert len(document["members"]) == 6
    assert "must not appear" not in (output / "manifest.json").read_text()
    assert "private label" not in (output / "manifest.json").read_text()
    assert "本预览没有运行告警" in (output / "preview.md").read_text()
    assert "每组最多10条" in (output / "preview.md").read_text()
    assert "本轮第一批统一积累" in (output / "preview.md").read_text()
    assert "批跑接入时实施" in (output / "preview.md").read_text()
    assert (output / "members.csv").is_file()
    assert (output / "groups.csv").is_file()
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
    with pytest.raises(FileExistsError):
        prepare(source, output)
    again = prepare(source, tmp_path / "again")
    assert again.plan_id == result.plan_id


@pytest.mark.parametrize("fault", ["source_hash", "source_size", "store_hash", "profile", "payload_identity", "store_source", "count", "filename_escape", "duplicate", "schema"])
def test_invalid_sources_fail_before_creating_preview(source, tmp_path, fault):
    pkl, store, index, document = source
    if fault == "source_hash":
        pkl.write_bytes(b"new source with same deployment name")
    elif fault == "source_size":
        document["source"]["size_bytes"] += 1
    elif fault == "store_hash":
        document["payload_store"]["sha256"] = "0" * 64
    elif fault == "profile":
        document["memory_profile"] = {**PROFILE, "profile_version": "old"}
    elif fault == "payload_identity":
        document["cases"][0]["payload_hash"] = "f" * 64
    elif fault == "store_source":
        with sqlite3.connect(store) as connection:
            connection.execute("UPDATE metadata SET value='wrong' WHERE key='source_sha256'")
        document["payload_store"]["sha256"] = digest(store)
    elif fault == "count":
        document["source"]["alert_count"] += 1
    elif fault == "filename_escape":
        document["payload_store"]["file_name"] = "../sample.workbench-payloads.sqlite"
    elif fault == "duplicate":
        document["cases"][1]["alert_id"] = document["cases"][0]["alert_id"]
    elif fault == "schema":
        document["schema_version"] = "unsupported"
    index.write_text(json.dumps(document), encoding="utf-8")
    target = tmp_path / "preview"
    with pytest.raises(ValueError):
        prepare(source, target)
    assert not target.exists()


def test_labels_do_not_change_membership(source, tmp_path):
    first = prepare(source, tmp_path / "before")
    document = source[3]
    for row in document["cases"]:
        row["operational_label"] = "different"
        row["ground_label"] = "true_positive"
    source[2].write_text(json.dumps(document), encoding="utf-8")
    second = prepare(source, tmp_path / "after")
    assert first.members == second.members
    assert first.plan_id != second.plan_id  # The manifest still pins exact index bytes.


def test_cli_uses_current_static_profile_and_checkout_relative_outputs(source, tmp_path, monkeypatch, capsys):
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/soc_corpus_batch_preview.py"))["main"]
    monkeypatch.setitem(main.__globals__, "BACKEND_ROOT", tmp_path)
    monkeypatch.setitem(main.__globals__, "PingAnSocMemoryProfile", SimpleNamespace(identity=SocMemoryProfileIdentity(**PROFILE)))
    target = tmp_path / ".deer-flow/preview"
    arguments = ["--source", str(source[0]), "--index", str(source[2]), "--output-dir", str(target)]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["alerts_run"] == 0
    assert main(arguments) == 1
    assert "already exists" in capsys.readouterr().err


def test_uncheckpointed_store_and_invalid_rows_are_not_silently_accepted(source, tmp_path):
    wal = source[1].with_name(source[1].name + "-wal")
    wal.write_bytes(b"unfrozen database state")
    with pytest.raises(ValueError, match="sidecar"):
        prepare(source, tmp_path / "wal-preview")
    wal.unlink()
    source[3]["cases"][0] = None
    source[2].write_text(json.dumps(source[3]), encoding="utf-8")
    with pytest.raises(ValueError, match="case must be an object"):
        prepare(source, tmp_path / "row-preview")


def test_csv_formula_like_rule_names_are_literal(source, tmp_path):
    import csv

    source[3]["cases"][0]["rule_name"] = '=HYPERLINK("example.invalid")'
    source[2].write_text(json.dumps(source[3]), encoding="utf-8")
    prepare(source, tmp_path / "preview")
    with (tmp_path / "preview/members.csv").open(encoding="utf-8-sig") as stream:
        row = next(csv.DictReader(stream))
    assert row["规则名称"].startswith("'=")
