"""Observation replacement preserves history and counts each source once."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex
from test_soc_memory_patterns import _observe, _run, _service

from soc_agent.contracts.memory_patterns import MemoryPatternAccumulationScope
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.db.migration_runner import upgrade_soc_schema
from soc_agent.db.models import SocMemoryPatternObservationRow
from soc_agent.db.repositories import _memory_pattern_observation_row_values
from soc_agent.memory.patterns import InMemoryMemoryPatternRepository


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        yield InMemoryMemoryPatternRepository()
        return
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'observations.db'}")
    create_soc_tables(engine)
    yield SqlAlchemyAlertRepository(sessionmaker(bind=engine))
    engine.dispose()


def observation(repository):
    return _observe(_service(repository, threshold=100), _run(1), transport_ref="first").observation


def revised(original, *, changed_group=False, suffix="2"):
    return original.model_copy(
        update={
            "observation_id": f"MPO-REVISION-{suffix}",
            "idempotency_key": f"observation-revision:{suffix}",
            "content_hash": suffix.zfill(64),
            "source": original.source.model_copy(update={"run_id": f"RUN-REVISION-{suffix}"}),
            **({"aggregation_key": "a" * 64, "lineage_key": "b" * 64} if changed_group else {}),
        }
    )


@pytest.mark.parametrize("changed_group", [False, True])
def test_replacement_counts_latest_once_and_retains_old_payload(repository, changed_group):
    old = observation(repository)
    frozen = old.model_dump(mode="json")
    new = revised(old, changed_group=changed_group)
    repository.replace_memory_pattern_observations(new, superseded_ids=[old.observation_id])
    repository.replace_memory_pattern_observations(new, superseded_ids=[old.observation_id])

    assert repository.get_memory_pattern_observation(old.observation_id).model_dump(mode="json") == frozen
    assert repository.find_memory_pattern_observation_by_idempotency_key(old.idempotency_key) == old
    assert not repository.is_memory_pattern_observation_current(old.observation_id)
    assert repository.is_memory_pattern_observation_current(new.observation_id)
    assert repository.list_memory_pattern_observations() == [new]
    assert {item.observation_id for item in repository.list_memory_pattern_observations(include_superseded=True)} == {old.observation_id, new.observation_id}
    if changed_group:
        assert repository.list_memory_pattern_observations(aggregation_key=old.aggregation_key) == []
    inventory = repository.get_memory_center_inventory()
    assert inventory.observation_count == inventory.pattern_count == inventory.aggregation_window_count == 1
    assert sum(item.observation_count for item in inventory.profile_inventory) == 1
    stats = repository.list_memory_pattern_lineage_stats()
    assert stats.total == 1
    assert stats.items[0].lineage_key == new.lineage_key
    assert stats.items[0].support_count == stats.items[0].distinct_source_count == 1


def test_replacement_compare_and_swap_rejects_stale_predecessor_atomically(repository):
    old = observation(repository)
    new = revised(old)
    repository.replace_memory_pattern_observations(new, superseded_ids=[old.observation_id])
    competing = revised(old, suffix="3")
    with pytest.raises(ValueError, match="current|supersed"):
        repository.replace_memory_pattern_observations(competing, superseded_ids=[old.observation_id])
    assert repository.get_memory_pattern_observation(competing.observation_id) is None
    assert repository.list_memory_pattern_observations() == [new]
    with pytest.raises(ValueError):
        repository.replace_memory_pattern_observations(new, superseded_ids=["MPO-MISSING"])
    assert repository.list_memory_pattern_observations() == [new]


@pytest.mark.parametrize("changed", ["tenant_id", "environment", "source_id", "alert_id"])
def test_replacement_rejects_unrelated_source_without_withdrawing_old(repository, changed):
    old = observation(repository)
    new = revised(old)
    if changed in {"source_id", "alert_id"}:
        new = new.model_copy(update={"source": new.source.model_copy(update={changed: "unrelated"})})
    else:
        new = new.model_copy(update={changed: "unrelated"})
    with pytest.raises(ValueError, match="scope|source|identity"):
        repository.replace_memory_pattern_observations(new, superseded_ids=[old.observation_id])
    assert repository.list_memory_pattern_observations() == [old]
    assert repository.get_memory_pattern_observation(new.observation_id) is None


def test_replacement_rolls_back_when_new_current_identity_conflicts(repository):
    old = observation(repository)
    unrelated = _observe(_service(repository, threshold=100), _run(2), transport_ref="other").observation
    new = revised(old).model_copy(update={"occurrence_key": unrelated.occurrence_key})
    with pytest.raises(ValueError):
        repository.replace_memory_pattern_observations(new, superseded_ids=[old.observation_id])
    assert repository.is_memory_pattern_observation_current(old.observation_id)
    assert len(repository.list_memory_pattern_observations()) == 2


@pytest.mark.parametrize("change", [None, "experiment_id", "learning_generation", "accumulation_mode"])
def test_continuous_scope_can_adopt_legacy_observation_only_within_same_generation(repository, change):
    original = observation(InMemoryMemoryPatternRepository())
    scope = MemoryPatternAccumulationScope(
        experiment_id="EXP-LEARNING",
        learning_generation="GEN-1",
        configuration_hash="a" * 64,
        event_start=original.window_start,
        event_end=original.window_end,
    )
    old = original.model_copy(update={"accumulation_scope": scope})
    repository.save_memory_pattern_observation(old)
    new_scope = scope.model_copy(update={"configuration_hash": None, "accumulation_mode": "continuous", "event_end": scope.event_end + timedelta(days=1)})
    if change:
        new_scope = new_scope.model_copy(update={change: None if change == "accumulation_mode" else "other"})
    new = revised(old, changed_group=True).model_copy(update={"accumulation_scope": new_scope, "window_end": new_scope.event_end})
    if change:
        with pytest.raises(ValueError, match="scope"):
            repository.replace_memory_pattern_observations(new, superseded_ids=[old.observation_id])
        assert repository.list_memory_pattern_observations() == [old]
    else:
        repository.replace_memory_pattern_observations(new, superseded_ids=[old.observation_id])
        assert repository.list_memory_pattern_observations() == [new]
        assert repository.get_memory_pattern_observation(old.observation_id) == old


def test_postgresql_current_indexes_are_partial():
    indexes = {item.name: item for item in SocMemoryPatternObservationRow.__table__.indexes}
    for name in ("uq_soc_memory_pattern_current_source", "uq_soc_memory_pattern_current_occurrence"):
        ddl = str(CreateIndex(indexes[name]).compile(dialect=postgresql.dialect()))
        assert "CREATE UNIQUE INDEX" in ddl
        assert "WHERE is_current = true" in ddl


def test_migration_keeps_legacy_payload_current_and_replaces_unique_constraints(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}"
    upgrade_soc_schema(url, revision="0032_corpus_revision_index")
    old = observation(InMemoryMemoryPatternRepository())
    frozen = old.model_dump(mode="json")
    engine = create_engine(url)
    table = Table("soc_memory_pattern_observations", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(observation_id=old.observation_id, **_memory_pattern_observation_row_values(old, frozen)))
    upgrade_soc_schema(url)
    upgrade_soc_schema(url)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine))
    assert repository.get_memory_pattern_observation(old.observation_id).model_dump(mode="json") == frozen
    assert repository.is_memory_pattern_observation_current(old.observation_id)
    repository.replace_memory_pattern_observations(revised(old), superseded_ids=[old.observation_id])
    with engine.connect() as connection:
        assert json.loads(connection.scalar(text("SELECT observation_payload FROM soc_memory_pattern_observations WHERE observation_id = :id"), {"id": old.observation_id})) == frozen
        assert connection.scalar(select(SocMemoryPatternObservationRow.superseded_by_observation_id).where(SocMemoryPatternObservationRow.observation_id == old.observation_id)) == "MPO-REVISION-2"
        indexes = inspect(connection).get_indexes(table.name)
        assert {item["name"] for item in indexes if item["unique"]} >= {"uq_soc_memory_pattern_current_source", "uq_soc_memory_pattern_current_occurrence"}
        assert all(item["column_names"] not in (["aggregation_key", "source_id"], ["aggregation_key", "occurrence_key"]) for item in inspect(connection).get_unique_constraints(table.name))
    engine.dispose()
