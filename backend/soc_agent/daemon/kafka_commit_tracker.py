"""Partition-aware Kafka commit tracking for future SOC worker pools."""

from __future__ import annotations

from dataclasses import dataclass, field

from soc_agent.daemon.kafka_mapper import KafkaRecord


@dataclass(frozen=True)
class KafkaPartitionRef:
    """Kafka topic/partition identity independent of any broker SDK."""

    topic: str
    partition: int


@dataclass(frozen=True)
class KafkaCommitAdvance:
    """Commit target for one partition.

    ``offset`` follows Kafka commit semantics: it is the next offset to consume,
    not the last processed offset.
    """

    topic: str
    partition: int
    offset: int


@dataclass(frozen=True)
class PartitionCommitStateSnapshot:
    """Read-only snapshot used by tests and future metrics."""

    topic: str
    partition: int
    next_committable_offset: int
    in_flight_offsets: tuple[int, ...] = ()
    completed_offsets: tuple[int, ...] = ()
    dead_letter_pending_offsets: tuple[int, ...] = ()


@dataclass
class _PartitionCommitState:
    next_committable_offset: int
    in_flight_offsets: set[int] = field(default_factory=set)
    completed_offsets: set[int] = field(default_factory=set)
    dead_letter_pending_offsets: set[int] = field(default_factory=set)


class PartitionCommitTracker:
    """Tracks safe commit advancement for out-of-order worker completion.

    The tracker intentionally does not poll, commit, or dead-letter Kafka
    records. It only answers: "after these completed offsets, what is the
    highest safe Kafka commit offset per partition?"
    """

    def __init__(self) -> None:
        self._states: dict[KafkaPartitionRef, _PartitionCommitState] = {}

    def mark_in_flight(self, record: KafkaRecord) -> None:
        """Register that a record has been handed to a worker."""

        state = self._state_for(record)
        if record.offset in state.completed_offsets:
            raise ValueError(f"offset already completed: {record.topic}:{record.partition}:{record.offset}")
        if record.offset in state.dead_letter_pending_offsets:
            raise ValueError(f"offset already pending dead-letter: {record.topic}:{record.partition}:{record.offset}")
        state.in_flight_offsets.add(record.offset)

    def mark_processed(self, record: KafkaRecord) -> list[KafkaCommitAdvance]:
        """Mark successful processing and return safe commit advances."""

        state = self._state_for(record)
        state.in_flight_offsets.discard(record.offset)
        state.completed_offsets.add(record.offset)
        return self._advance(record)

    def mark_dead_letter_pending(self, record: KafkaRecord) -> None:
        """Mark an offset as waiting for dead-letter publication.

        Pending offsets are not committable. Call ``mark_dead_lettered`` only
        after dead-letter publish/flush succeeds.
        """

        state = self._state_for(record)
        if record.offset in state.completed_offsets:
            raise ValueError(f"offset already completed: {record.topic}:{record.partition}:{record.offset}")
        state.in_flight_offsets.discard(record.offset)
        state.dead_letter_pending_offsets.add(record.offset)

    def mark_dead_lettered(self, record: KafkaRecord) -> list[KafkaCommitAdvance]:
        """Mark dead-letter publication success and return safe advances."""

        state = self._state_for(record)
        if record.offset not in state.dead_letter_pending_offsets:
            raise ValueError(f"offset is not pending dead-letter: {record.topic}:{record.partition}:{record.offset}")
        state.dead_letter_pending_offsets.remove(record.offset)
        state.completed_offsets.add(record.offset)
        return self._advance(record)

    def snapshot(self, partition: KafkaPartitionRef) -> PartitionCommitStateSnapshot | None:
        state = self._states.get(partition)
        if state is None:
            return None
        return PartitionCommitStateSnapshot(
            topic=partition.topic,
            partition=partition.partition,
            next_committable_offset=state.next_committable_offset,
            in_flight_offsets=tuple(sorted(state.in_flight_offsets)),
            completed_offsets=tuple(sorted(state.completed_offsets)),
            dead_letter_pending_offsets=tuple(sorted(state.dead_letter_pending_offsets)),
        )

    def _state_for(self, record: KafkaRecord) -> _PartitionCommitState:
        partition = KafkaPartitionRef(topic=record.topic, partition=record.partition)
        state = self._states.get(partition)
        if state is None:
            state = _PartitionCommitState(next_committable_offset=record.offset)
            self._states[partition] = state
            return state
        if record.offset < state.next_committable_offset:
            raise ValueError(f"offset is behind committed boundary: {record.topic}:{record.partition}:{record.offset} < {state.next_committable_offset}")
        return state

    def _advance(self, record: KafkaRecord) -> list[KafkaCommitAdvance]:
        partition = KafkaPartitionRef(topic=record.topic, partition=record.partition)
        state = self._states[partition]
        original = state.next_committable_offset
        while state.next_committable_offset in state.completed_offsets:
            state.completed_offsets.remove(state.next_committable_offset)
            state.next_committable_offset += 1
        if state.next_committable_offset == original:
            return []
        return [
            KafkaCommitAdvance(
                topic=record.topic,
                partition=record.partition,
                offset=state.next_committable_offset,
            )
        ]
