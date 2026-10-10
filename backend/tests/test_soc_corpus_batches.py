from datetime import UTC, datetime, timedelta

import pytest

from soc_agent.demo.corpus_batches import CorpusBatchCase, CorpusBatchPlan, build_corpus_batch_plan


def cases(count, group="group", *, start=0):
    return [
        CorpusBatchCase(
            alert_id=str(start + i),
            source_index=start + i,
            payload_hash=f"{start + i:064x}",
            observed_at=(datetime(2026, 8, 1, tzinfo=UTC) + timedelta(hours=i)).isoformat(),
            group_id=group,
            window_id="window",
            behavior_fingerprint="fingerprint",
            decision_eligible=True,
        )
        for i in range(count)
    ]


def plan(rows):
    return build_corpus_batch_plan(rows, source_identity={"sha256": "a" * 64})


def test_groupwise_chronology_and_complete_disjoint_partitions():
    rows = cases(10, "a") + cases(6, "b", start=10) + cases(5, "c", start=16) + cases(1, "d", start=21)
    result = plan(rows)
    assert result == plan(reversed(rows))
    assert result.counts == {"learning": 13, "validation_main": 3, "validation_supplementary": 6, "total": 22}
    assert len({m.alert_id for m in result.members}) == 22
    by_id = {m.alert_id: m for m in result.members}
    assert by_id["6"].batch == "learning"
    assert by_id["7"].batch == "learning"
    assert by_id["8"].validation_tier == "main"
    assert by_id["14"].batch == "learning"
    assert by_id["15"].validation_tier == "main"
    assert by_id["16"].validation_tier == "supplementary"
    assert by_id["20"].reason == "small_group"
    assert by_id["20"].validation_tier == "supplementary"
    assert by_id["21"].reason == "singleton"
    assert by_id["21"].validation_tier == "supplementary"
    for group in result.groups:
        members = [m for m in result.members if m.group_id == group.group_id]
        assert len(members) == group.total
        if group.learning_count and group.validation_main_count:
            assert group.learning_last_at <= group.validation_first_at


@pytest.mark.parametrize("count,learning", [(6, 5), (8, 7), (10, 8), (14, 12), (15, 12), (52, 42), (514, 412)])
def test_learning_first_keeps_earliest_samples_without_a_per_group_cap(count, learning):
    rows = cases(count)
    result = plan(rows)
    assert result == plan(reversed(rows))
    assert result.counts == {"learning": learning, "validation_main": count - learning, "validation_supplementary": 0, "total": count}
    assert [m.alert_id for m in result.members if m.batch == "learning"] == [r.alert_id for r in rows[:learning]]
    assert [m.alert_id for m in result.members if m.validation_tier == "main"] == [r.alert_id for r in rows[learning:]]
    assert result.max_learning_per_group is None


def test_experiment_aggregation_is_planned_without_rewriting_event_times_or_normal_windows():
    rows = cases(10)
    for i, row in enumerate(rows):
        row.observed_at = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=35 * i)).isoformat()
        row.window_id = f"normal-window-{i}"
    result = plan(rows)
    assert result.schema_version == "soc.corpus_group_time_split.v3"
    assert result.split_method == "per_group_time_80_20_main_learning_first"
    assert result.learning_aggregation == "experiment_actual_pattern"
    assert result.learning_aggregation_status == "planned"
    assert result.execution_enabled is False
    assert result.groups[0].ordinary_window_max_count == 1
    assert result.groups[0].warnings == []
    assert result.counts["learning"] == 8
    assert [(m.observed_at, m.window_id) for m in result.members] == [(r.observed_at, r.window_id) for r in rows]
    assert [m.event_time_utc for m in result.members] == [datetime.fromisoformat(r.observed_at) for r in rows]


def test_same_timestamp_has_stable_id_tie_break_without_claiming_strict_future():
    rows = cases(6)
    for row in rows:
        row.observed_at = "2026-08-01T08:00:00+08:00"
    result = plan(rows)
    assert result == plan(reversed(rows))
    assert [m.alert_id for m in result.members if m.batch == "learning"] == ["0", "1", "2", "3", "4"]
    assert result.groups[0].boundary_same_time is True
    assert result.members[0].event_time_utc == datetime(2026, 8, 1, tzinfo=UTC)


@pytest.mark.parametrize("timestamp,reason", [(None, "event_time_missing"), ("broken", "event_time_invalid"), ("2026-08-01T00:00:00", "event_time_timezone_missing")])
def test_unknown_time_is_preserved_as_supplement_and_does_not_fill_learning_threshold(timestamp, reason):
    rows = cases(6)
    rows[-1].observed_at = timestamp
    result = plan(rows)
    assert result.counts["learning"] == 0
    assert result.counts["validation_supplementary"] == 6
    assert result.counts["validation_main"] == 0
    member = next(m for m in result.members if m.alert_id == "5")
    assert member.reason == reason
    assert member.observed_at == timestamp
    assert member.event_time_utc is None


def test_weak_groups_and_split_windows_are_reported_not_removed_from_main_denominator():
    rows = cases(6)
    for i, row in enumerate(rows):
        row.window_id = f"window-{i}"
        row.behavior_fingerprint = None
        row.decision_eligible = False
    result = plan(rows)
    assert result.counts["validation_main"] == 1
    assert result.groups[0].ordinary_window_max_count == 1
    assert set(result.groups[0].warnings) == {"fingerprint_missing", "direct_reuse_not_ready"}


def test_identity_binds_source_membership_group_and_payload():
    rows = cases(6)
    first = plan(rows)
    assert build_corpus_batch_plan(rows, source_identity={"sha256": "b" * 64}).plan_id != first.plan_id
    rows[0].payload_hash = "f" * 64
    assert plan(rows).plan_id != first.plan_id
    rows[0].group_id = "changed"
    assert plan(rows).plan_id != first.plan_id
    assert plan(rows).counts["learning"] == 0


def test_duplicate_id_is_rejected_without_dropping_source_rows():
    rows = cases(6)
    rows[1].alert_id = rows[0].alert_id
    with pytest.raises(ValueError, match="duplicate alert"):
        plan(rows)


def test_empty_corpus_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        plan([])


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_singletons_and_small_groups_remain_in_supplementary_validation(count):
    result = plan(cases(count))
    assert result.counts == {"learning": 0, "validation_main": 0, "validation_supplementary": count, "total": count}
    assert result.grouping_basis == "static_workbench_index"
    assert len(result.groups) == 1
    assert all(m.reason == ("singleton" if count == 1 else "small_group") for m in result.members)


def test_all_unknown_times_remain_supplementary():
    rows = cases(10)
    for row in rows:
        row.observed_at = None
    result = plan(rows)
    assert result.counts == {"learning": 0, "validation_main": 0, "validation_supplementary": 10, "total": 10}
    assert result.groups[0].warnings == ["event_time_unusable"]


@pytest.mark.parametrize("excluded", [set(), {"0", "2"}])
def test_only_original_main_validation_members_can_move_to_learning(excluded):
    for count in range(1, 120):
        valid_rows = cases(count)
        invalid_rows = cases(3, start=count)
        for row, timestamp in zip(invalid_rows, [None, "broken", "2026-08-01T00:00:00"], strict=True):
            row.observed_at = timestamp
        rows = valid_rows + invalid_rows
        old_cut = min(10, count - 1, max(5, count * 7 // 10)) if count >= 6 else 0
        old_learning = {row.alert_id for row in valid_rows[:old_cut]} - excluded
        old_main = ({row.alert_id for row in valid_rows[old_cut:]} if old_cut else set()) - excluded
        old_supplementary = {row.alert_id for row in rows} - excluded - old_learning - old_main
        result = build_corpus_batch_plan(reversed(rows), source_identity={"sha256": "a" * 64}, excluded_alert_ids=excluded)
        new_learning = {row.alert_id for row in result.members if row.batch == "learning"}
        new_main = {row.alert_id for row in result.members if row.validation_tier == "main"}
        new_supplementary = {row.alert_id for row in result.members if row.validation_tier == "supplementary"}
        assert old_learning <= new_learning
        assert new_supplementary == old_supplementary
        assert new_learning - old_learning <= old_main
        assert new_main == old_main - new_learning
        assert new_learning | new_main | new_supplementary == {row.alert_id for row in rows} - excluded


def test_exclusion_keeps_existing_split_positions_and_never_promotes_a_holdout():
    rows = cases(10, "large") + cases(1, "singleton", start=10)
    full = plan(rows)
    excluded = {"0", "1", "2", "3", "4", "10"}
    scoped = build_corpus_batch_plan(rows, source_identity=full.source_identity, excluded_alert_ids=excluded)
    assert scoped.members == [m for m in full.members if m.alert_id not in excluded]
    assert scoped.counts == {"learning": 3, "validation_main": 2, "validation_supplementary": 0, "total": 5}
    assert [g.group_id for g in scoped.groups] == ["large"]
    assert scoped.plan_id != full.plan_id


def test_plan_policy_identity_changes_without_changing_static_groups_or_facts():
    current = plan(cases(6))
    previous = current.model_dump(mode="json", exclude={"plan_id"})
    previous.update(schema_version="soc.corpus_group_time_split.v2", split_method="per_group_time_70_30_min_five_max_ten", max_learning_per_group=10)
    from soc_agent.utils.hashing import stable_hash

    assert current.plan_id != stable_hash(previous)
    for source, member in zip(cases(6), current.members, strict=True):
        assert member.model_dump(include=set(CorpusBatchCase.model_fields)) == source.model_dump()


def test_historical_plan_policy_metadata_remains_readable():
    current = plan(cases(6))
    old = current.model_dump()
    old.update(schema_version="soc.corpus_group_time_split.v2", split_method="per_group_time_70_30_min_five_max_ten", max_learning_per_group=10)
    assert CorpusBatchPlan.model_validate(old).model_dump() == old
    del old["split_method"]
    del old["max_learning_per_group"]
    loaded = CorpusBatchPlan.model_validate(old)
    assert loaded.split_method == "per_group_time_70_30_min_five_max_ten"
    assert loaded.max_learning_per_group == 10


@pytest.mark.parametrize("field,value", [("split_method", "per_group_time_70_30_min_five_max_ten"), ("max_learning_per_group", 10)])
def test_current_plan_rejects_mixed_historical_policy_metadata(field, value):
    data = plan(cases(6)).model_dump()
    data[field] = value
    with pytest.raises(ValueError, match="split policy"):
        CorpusBatchPlan.model_validate(data)
