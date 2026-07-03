from __future__ import annotations

import pytest

from soc_agent.daemon import KafkaPartitionRef, PartitionCommitTracker
from soc_agent.daemon.kafka_mapper import KafkaRecord


def _record(offset: int, *, topic: str = "soc.alerts.raw.v1", partition: int = 0) -> KafkaRecord:
    return KafkaRecord(topic=topic, partition=partition, offset=offset, value="{}")


def test_tracker_does_not_advance_past_missing_lower_offset() -> None:
    tracker = PartitionCommitTracker()
    first = _record(9)
    second = _record(10)

    tracker.mark_in_flight(first)
    tracker.mark_in_flight(second)

    assert tracker.mark_processed(second) == []

    snapshot = tracker.snapshot(KafkaPartitionRef(topic=first.topic, partition=first.partition))
    assert snapshot is not None
    assert snapshot.next_committable_offset == 9
    assert snapshot.completed_offsets == (10,)
    assert snapshot.in_flight_offsets == (9,)


def test_tracker_advances_across_contiguous_completed_offsets() -> None:
    tracker = PartitionCommitTracker()
    first = _record(9)
    second = _record(10)

    tracker.mark_in_flight(first)
    tracker.mark_in_flight(second)
    assert tracker.mark_processed(second) == []

    advances = tracker.mark_processed(first)

    assert len(advances) == 1
    assert advances[0].topic == "soc.alerts.raw.v1"
    assert advances[0].partition == 0
    assert advances[0].offset == 11

    snapshot = tracker.snapshot(KafkaPartitionRef(topic=first.topic, partition=first.partition))
    assert snapshot is not None
    assert snapshot.next_committable_offset == 11
    assert snapshot.completed_offsets == ()
    assert snapshot.in_flight_offsets == ()


def test_tracker_keeps_dead_letter_pending_offset_uncommittable() -> None:
    tracker = PartitionCommitTracker()
    failed = _record(1)
    later = _record(2)

    tracker.mark_in_flight(failed)
    tracker.mark_in_flight(later)
    tracker.mark_dead_letter_pending(failed)

    assert tracker.mark_processed(later) == []

    snapshot = tracker.snapshot(KafkaPartitionRef(topic=failed.topic, partition=failed.partition))
    assert snapshot is not None
    assert snapshot.next_committable_offset == 1
    assert snapshot.dead_letter_pending_offsets == (1,)
    assert snapshot.completed_offsets == (2,)


def test_tracker_advances_after_dead_letter_publish_succeeds() -> None:
    tracker = PartitionCommitTracker()
    failed = _record(1)
    later = _record(2)

    tracker.mark_in_flight(failed)
    tracker.mark_in_flight(later)
    tracker.mark_dead_letter_pending(failed)
    assert tracker.mark_processed(later) == []

    advances = tracker.mark_dead_lettered(failed)

    assert len(advances) == 1
    assert advances[0].offset == 3

    snapshot = tracker.snapshot(KafkaPartitionRef(topic=failed.topic, partition=failed.partition))
    assert snapshot is not None
    assert snapshot.next_committable_offset == 3
    assert snapshot.dead_letter_pending_offsets == ()
    assert snapshot.completed_offsets == ()


def test_tracker_tracks_partitions_independently() -> None:
    tracker = PartitionCommitTracker()
    partition_zero = _record(5, partition=0)
    partition_one = _record(20, partition=1)

    tracker.mark_in_flight(partition_zero)
    tracker.mark_in_flight(partition_one)

    advances = tracker.mark_processed(partition_one)

    assert len(advances) == 1
    assert advances[0].partition == 1
    assert advances[0].offset == 21

    snapshot_zero = tracker.snapshot(KafkaPartitionRef(topic=partition_zero.topic, partition=0))
    snapshot_one = tracker.snapshot(KafkaPartitionRef(topic=partition_one.topic, partition=1))
    assert snapshot_zero is not None
    assert snapshot_one is not None
    assert snapshot_zero.next_committable_offset == 5
    assert snapshot_zero.in_flight_offsets == (5,)
    assert snapshot_one.next_committable_offset == 21


def test_tracker_rejects_offsets_behind_committed_boundary() -> None:
    tracker = PartitionCommitTracker()
    record = _record(7)
    tracker.mark_in_flight(record)
    assert tracker.mark_processed(record)[0].offset == 8

    with pytest.raises(ValueError, match="behind committed boundary"):
        tracker.mark_in_flight(_record(7))


def test_tracker_rejects_dead_letter_completion_without_pending_state() -> None:
    tracker = PartitionCommitTracker()
    record = _record(1)
    tracker.mark_in_flight(record)

    with pytest.raises(ValueError, match="not pending dead-letter"):
        tracker.mark_dead_lettered(record)
