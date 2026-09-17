from datetime import UTC, datetime, timedelta

import pytest

from soc_agent.demo.corpus_batches import CorpusBatchCase, build_corpus_batch_plan


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
    assert result.counts == {"learning": 12, "validation_main": 4, "validation_supplementary": 6, "total": 22}
    assert len({m.alert_id for m in result.members}) == 22
    by_id = {m.alert_id: m for m in result.members}
    assert by_id["6"].batch == "learning"
    assert by_id["7"].validation_tier == "main"
    assert by_id["14"].batch == "learning"
    assert by_id["15"].validation_tier == "main"
    assert by_id["16"].reason == "small_group"
    assert by_id["21"].reason == "singleton"
    for group in result.groups:
        members = [m for m in result.members if m.group_id == group.group_id]
        assert len(members) == group.total
        if group.learning_count:
            assert group.learning_last_at <= group.validation_first_at


@pytest.mark.parametrize("count,learning", [(6, 5), (10, 7), (14, 9), (15, 10), (52, 10), (514, 10)])
def test_learning_cap_keeps_earliest_samples_and_all_remaining_holdouts(count, learning):
    rows = cases(count)
    result = plan(rows)
    assert result == plan(reversed(rows))
    assert result.counts == {"learning": learning, "validation_main": count - learning, "validation_supplementary": 0, "total": count}
    assert [m.alert_id for m in result.members if m.batch == "learning"] == [r.alert_id for r in rows[:learning]]
    assert [m.alert_id for m in result.members if m.validation_tier == "main"] == [r.alert_id for r in rows[learning:]]
    assert result.max_learning_per_group == 10


def test_experiment_aggregation_is_planned_without_rewriting_event_times_or_normal_windows():
    rows = cases(10)
    for i, row in enumerate(rows):
        row.observed_at = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=35 * i)).isoformat()
        row.window_id = f"normal-window-{i}"
    result = plan(rows)
    assert result.schema_version == "soc.corpus_group_time_split.v2"
    assert result.learning_aggregation == "experiment_actual_pattern"
    assert result.learning_aggregation_status == "planned"
    assert result.execution_enabled is False
    assert result.groups[0].ordinary_window_max_count == 1
    assert result.groups[0].warnings == []
    assert result.counts["learning"] == 7
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
    assert plan(rows).counts["learning"] == 0


def test_duplicate_id_is_rejected_without_dropping_source_rows():
    rows = cases(6)
    rows[1].alert_id = rows[0].alert_id
    with pytest.raises(ValueError, match="duplicate alert"):
        plan(rows)


def test_empty_corpus_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        plan([])
