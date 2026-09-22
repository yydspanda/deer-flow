import json
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event

import pytest

from soc_agent.demo.corpus_capacity import CorpusCapacity


def test_limit_persists_across_restart_without_database_writes(tmp_path):
    path = tmp_path / "soc_agent_dev.capacity.json"
    capacity = CorpusCapacity(8, path)
    assert capacity.ceiling == capacity.max_concurrency == 8
    assert not path.exists()

    capacity.set_limit(3, actor_id="host-operator")

    assert capacity.max_concurrency == 3
    assert CorpusCapacity(8, path).max_concurrency == 3
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "soc.corpus_capacity.v1"
    assert payload["max_concurrency"] == 3
    assert payload["updated_by"] == "host-operator"
    assert datetime.fromisoformat(payload["updated_at"]).utcoffset().total_seconds() == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [path]


def test_limit_is_bounded_by_deployment_and_global_ceiling(tmp_path):
    path = tmp_path / "capacity.json"
    capacity = CorpusCapacity(12, path)
    assert capacity.ceiling == capacity.max_concurrency == 8
    capacity.set_limit(7, actor_id="operator")

    lower_deployment = CorpusCapacity(4, path)
    assert lower_deployment.ceiling == lower_deployment.max_concurrency == 4
    assert json.loads(path.read_text(encoding="utf-8"))["max_concurrency"] == 7
    with pytest.raises(ValueError, match="1.*4"):
        lower_deployment.set_limit(5, actor_id="operator")


@pytest.mark.parametrize("value", [True, False, 0, -1, 9, 3.0, "3", None])
def test_invalid_limit_does_not_change_memory_or_saved_value(tmp_path, value):
    path = tmp_path / "capacity.json"
    capacity = CorpusCapacity(8, path)
    capacity.set_limit(2, actor_id="operator")
    saved = path.read_bytes()

    with pytest.raises(ValueError, match="integer"):
        capacity.set_limit(value, actor_id="operator")

    assert capacity.max_concurrency == 2
    assert path.read_bytes() == saved


@pytest.mark.parametrize("ceiling", [True, False, 0, -1, 3.0, "3", None])
def test_invalid_deployment_ceiling_fails_clearly(ceiling):
    with pytest.raises(ValueError, match="ceiling"):
        CorpusCapacity(ceiling)


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_failed_persistence_preserves_previous_limit_and_cleans_temporary_file(tmp_path, monkeypatch, failure):
    path = tmp_path / "capacity.json"
    capacity = CorpusCapacity(8, path)
    capacity.set_limit(2, actor_id="operator")
    saved = path.read_bytes()

    def fail(*_):
        raise OSError("simulated write failure")

    monkeypatch.setattr(f"soc_agent.demo.corpus_capacity.os.{failure}", fail)
    with pytest.raises(OSError, match="simulated write failure"):
        capacity.set_limit(5, actor_id="operator")

    assert capacity.max_concurrency == 2
    assert path.read_bytes() == saved
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("payload", ["not json", "[]", "{}", '{"schema_version":"soc.corpus_capacity.v2","max_concurrency":2}'])
def test_corrupt_or_unknown_configuration_is_not_silently_ignored(tmp_path, payload):
    path = tmp_path / "capacity.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="capacity"):
        CorpusCapacity(8, path)


def test_non_utf8_capacity_file_fails_clearly(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_bytes(b"\xff")

    with pytest.raises(ValueError, match="capacity"):
        CorpusCapacity(8, path)


def test_new_limit_is_published_only_after_atomic_replacement(tmp_path, monkeypatch):
    path = tmp_path / "capacity.json"
    capacity = CorpusCapacity(8, path)
    capacity.set_limit(2, actor_id="operator")
    from soc_agent.demo import corpus_capacity

    replace = corpus_capacity.os.replace

    def inspect_replace(source, destination):
        assert capacity.max_concurrency == 2
        assert CorpusCapacity(8, path).max_concurrency == 2
        assert json.loads(source.read_text(encoding="utf-8"))["max_concurrency"] == 5
        replace(source, destination)

    monkeypatch.setattr(corpus_capacity.os, "replace", inspect_replace)
    capacity.set_limit(5, actor_id="operator")
    assert capacity.max_concurrency == CorpusCapacity(8, path).max_concurrency == 5


@pytest.mark.parametrize("value", [True, 0, -1, 9, 2.0, "2", None])
def test_invalid_saved_limits_are_not_treated_as_defaults(tmp_path, value):
    path = tmp_path / "capacity.json"
    capacity = CorpusCapacity(8, path)
    capacity.set_limit(2, actor_id="operator")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["max_concurrency"] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="capacity"):
        CorpusCapacity(8, path)


def test_admission_and_limit_changes_share_a_short_lock(tmp_path):
    capacity = CorpusCapacity(8, tmp_path / "capacity.json")
    started, changed = Event(), Event()

    def update():
        started.set()
        capacity.set_limit(3, actor_id="operator")
        changed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with capacity.admission() as limit:
            assert limit == 8
            future = pool.submit(update)
            assert started.wait(2)
            assert not changed.wait(0.05)
            assert capacity.max_concurrency == 8
        future.result(timeout=2)
        with capacity.admission() as limit:
            assert limit == 3


def test_in_memory_capacity_never_creates_a_file(monkeypatch):
    def fail(*_):
        pytest.fail("in-memory capacity must not write a file")

    monkeypatch.setattr("soc_agent.demo.corpus_capacity.os.replace", fail)
    capacity = CorpusCapacity(8)
    capacity.set_limit(1, actor_id="operator")
    assert capacity.max_concurrency == 1
