"""Indexed experiment/round queries over the same SOC job and audit database."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, aliased

from soc_agent.contracts import SocMemoryCandidate, SocMemoryCandidateReviewStage, SocMemoryRecord
from soc_agent.contracts.corpus_experiments import (
    CorpusCandidatePage,
    CorpusExperiment,
    CorpusExperimentMember,
    CorpusMemberPage,
    CorpusRound,
    CorpusRoundBrief,
    CorpusRoundItem,
    CorpusRoundItemPage,
    CorpusRoundProgress,
    CorpusRoundSelection,
    RoundState,
)
from soc_agent.contracts.processing_jobs import ACTIVE_PROCESSING_JOB_STATUSES, ProcessingJobStatus
from soc_agent.db.jobs import _job_from_row
from soc_agent.db.models import SocCorpusExperimentMemberRow, SocCorpusExperimentRow, SocCorpusRoundItemRow, SocCorpusRoundRow, SocMemoryCandidateRow, SocMemoryRecordRow, SocProcessingJobRow
from soc_agent.utils.hashing import stable_hash


class CorpusExperimentConflict(ValueError):
    """Immutable membership or optimistic round state no longer matches."""


class SqlAlchemyCorpusExperimentRepository:
    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]):
        self._session_factory = session_factory

    def prepare(self, experiment: CorpusExperiment, members: Sequence[CorpusExperimentMember]) -> bool:
        if not members or len({m.alert_id for m in members}) != len(members) or len({m.sequence_number for m in members}) != len(members):
            raise ValueError("experiment requires nonempty, unambiguous membership")
        ordered = sorted(members, key=lambda row: row.sequence_number)
        identity = stable_hash({"experiment": experiment.model_dump(mode="json", exclude={"member_count", "created_at", "created_by"}), "members": [m.model_dump(mode="json") for m in ordered]})
        fixed = experiment.model_copy(update={"member_count": len(ordered)})
        with self._session_factory() as session:
            existing = session.get(SocCorpusExperimentRow, experiment.experiment_id)
            if existing is not None:
                if existing.manifest_hash != identity:
                    raise CorpusExperimentConflict("experiment already has a different fixed membership")
                return False
            session.add(SocCorpusExperimentRow(experiment_id=fixed.experiment_id, plan_id=fixed.plan_id, manifest_hash=identity, created_at=fixed.created_at, record_payload=fixed.model_dump(mode="json")))
            session.add_all(
                [
                    SocCorpusExperimentMemberRow(
                        experiment_id=fixed.experiment_id,
                        alert_id=m.alert_id,
                        sequence_number=m.sequence_number,
                        group_id=m.group_id,
                        batch=m.batch,
                        validation_tier=m.validation_tier,
                        rule_code=m.rule_code,
                        record_payload=m.model_dump(mode="json"),
                    )
                    for m in ordered
                ]
            )
            session.commit()
            return True

    def get_experiment(self, experiment_id: str) -> CorpusExperiment | None:
        with self._session_factory() as session:
            row = session.get(SocCorpusExperimentRow, experiment_id)
            return CorpusExperiment.model_validate(row.record_payload) if row else None

    def list_experiments(self, *, limit: int = 50, offset: int = 0) -> list[CorpusExperiment]:
        _page(limit, offset)
        with self._session_factory() as session:
            rows = session.scalars(select(SocCorpusExperimentRow).order_by(SocCorpusExperimentRow.created_at.desc(), SocCorpusExperimentRow.experiment_id).limit(limit).offset(offset))
            return [CorpusExperiment.model_validate(row.record_payload) for row in rows]

    def get_member(self, experiment_id: str, alert_id: str) -> CorpusExperimentMember | None:
        with self._session_factory() as session:
            row = session.get(SocCorpusExperimentMemberRow, (experiment_id, alert_id))
            return CorpusExperimentMember.model_validate(row.record_payload) if row else None

    def learning_event_range(self, experiment_id: str) -> tuple[datetime, datetime]:
        event_time = SocCorpusExperimentMemberRow.record_payload["event_time"].as_string()
        with self._session_factory() as session:
            first, last = session.execute(select(func.min(event_time), func.max(event_time)).where(SocCorpusExperimentMemberRow.experiment_id == experiment_id, SocCorpusExperimentMemberRow.batch == "learning")).one()
        if first is None or last is None:
            raise ValueError("learning experiment has no event times")
        return datetime.fromisoformat(first), datetime.fromisoformat(last)

    def _learning_candidate_scope(self, experiment_id: str):
        learning_runs = (
            select(SocProcessingJobRow.run_id)
            .join(SocCorpusRoundItemRow, SocCorpusRoundItemRow.job_id == SocProcessingJobRow.job_id)
            .join(SocCorpusRoundRow, SocCorpusRoundRow.round_id == SocCorpusRoundItemRow.round_id)
            .where(SocCorpusRoundRow.experiment_id == experiment_id, SocCorpusRoundRow.batch == "learning", SocProcessingJobRow.run_id.is_not(None))
        )
        return or_(
            SocMemoryCandidateRow.source_run_id.in_(learning_runs),
            SocMemoryCandidateRow.candidate_payload["metadata"]["experiment_id"].as_string() == experiment_id,
            SocMemoryCandidateRow.candidate_payload["source"]["metadata"]["experiment_id"].as_string() == experiment_id,
        )

    def list_learning_candidates(self, experiment_id: str, *, review_stage: SocMemoryCandidateReviewStage = SocMemoryCandidateReviewStage.PENDING, limit: int = 20, offset: int = 0) -> CorpusCandidatePage:
        _page(limit, offset)
        query = select(SocMemoryCandidateRow).where(self._learning_candidate_scope(experiment_id))
        states = {"pending": ("pending_review", "confirmed_candidate"), "confirmed": ("confirmed",), "closed": ("rejected", "superseded", "expired", "deprecated")}
        if review_stage != "all":
            query = query.where(SocMemoryCandidateRow.status.in_(states[review_stage]))
        with self._session_factory() as session:
            count = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(query.order_by(SocMemoryCandidateRow.created_at.desc(), SocMemoryCandidateRow.candidate_id).offset(offset).limit(limit))
            return CorpusCandidatePage(total=count or 0, items=[SocMemoryCandidate.model_validate(row.candidate_payload) for row in rows])

    def learning_candidate_ids(self, experiment_id: str, candidate_ids: Sequence[str]) -> set[str]:
        if len(candidate_ids) > 50:
            raise ValueError("candidate scope check is limited to 50 IDs")
        with self._session_factory() as session:
            return set(session.scalars(select(SocMemoryCandidateRow.candidate_id).where(self._learning_candidate_scope(experiment_id), SocMemoryCandidateRow.candidate_id.in_(candidate_ids))))

    def learning_memory_records(self, experiment_id: str) -> list[SocMemoryRecord]:
        query = select(SocMemoryRecordRow).join(SocMemoryCandidateRow, SocMemoryCandidateRow.candidate_id == SocMemoryRecordRow.source_candidate_id).where(self._learning_candidate_scope(experiment_id))
        with self._session_factory() as session:
            return [SocMemoryRecord.model_validate(row.record_payload) for row in session.scalars(query.order_by(SocMemoryRecordRow.memory_id))]

    def governance_hash(self, tenant_id: str) -> str:
        # Include disabled narrow scopes: they still constrain broader exact reuse.
        query = (
            select(SocMemoryRecordRow.memory_id, SocMemoryRecordRow.version, SocMemoryRecordRow.content_hash, SocMemoryRecordRow.facets_hash, SocMemoryRecordRow.status, SocMemoryRecordRow.retrieval_enabled, SocMemoryRecordRow.updated_at)
            .where(or_(SocMemoryRecordRow.tenant_id == tenant_id, SocMemoryRecordRow.tenant_id.is_(None)))
            .order_by(SocMemoryRecordRow.memory_id)
        )
        with self._session_factory() as session:
            return stable_hash([list(row) for row in session.execute(query)])

    def list_members(self, experiment_id: str, *, selection: CorpusRoundSelection, limit: int = 100, offset: int = 0) -> CorpusMemberPage:
        _page(limit, offset)
        query = _members_query(experiment_id, selection)
        with self._session_factory() as session:
            count = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(query.order_by(SocCorpusExperimentMemberRow.sequence_number).offset(offset).limit(limit))
            return CorpusMemberPage(total=count or 0, items=[CorpusExperimentMember.model_validate(row.record_payload) for row in rows])

    def create_round(self, round_: CorpusRound) -> None:
        with self._session_factory() as session:
            if session.get(SocCorpusExperimentRow, round_.experiment_id) is None:
                raise ValueError("experiment not found")
            if round_.parent_round_id:
                parent = session.get(SocCorpusRoundRow, round_.parent_round_id)
                if parent is None or parent.experiment_id != round_.experiment_id:
                    raise ValueError("parent round must belong to this experiment")
            existing = session.get(SocCorpusRoundRow, round_.round_id)
            if existing is not None:
                if existing.record_payload != round_.model_dump(mode="json"):
                    raise CorpusExperimentConflict("round already exists with a different snapshot")
                return
            session.add(
                SocCorpusRoundRow(
                    round_id=round_.round_id, experiment_id=round_.experiment_id, batch=round_.selection.batch, state=round_.state, version=round_.version, created_at=round_.created_at, record_payload=round_.model_dump(mode="json")
                )
            )
            session.commit()

    def attach_job(self, round_id: str, alert_id: str, job_id: str, *, sequence_number: int) -> None:
        self.attach_jobs(round_id, [(alert_id, job_id, sequence_number)])

    def attach_jobs(self, round_id: str, items: Sequence[tuple[str, str, int]]) -> None:
        if not 1 <= len(items) <= 500 or len({item[0] for item in items}) != len(items):
            raise ValueError("attach_jobs requires 1..500 distinct alerts")
        with self._session_factory() as session:
            round_row = session.get(SocCorpusRoundRow, round_id)
            if round_row is None or round_row.state != "prepared":
                raise CorpusExperimentConflict("members can only be attached before the round starts")
            round_ = CorpusRound.model_validate(round_row.record_payload)
            alert_ids = [item[0] for item in items]
            members = {row.alert_id: row for row in session.scalars(_members_query(round_row.experiment_id, round_.selection).where(SocCorpusExperimentMemberRow.alert_id.in_(alert_ids)))}
            jobs = {row.job_id: row for row in session.scalars(select(SocProcessingJobRow).where(SocProcessingJobRow.job_id.in_([item[1] for item in items])))}
            existing = {row.alert_id: row for row in session.scalars(select(SocCorpusRoundItemRow).where(SocCorpusRoundItemRow.round_id == round_id, SocCorpusRoundItemRow.alert_id.in_(alert_ids)))}
            for alert_id, job_id, sequence_number in items:
                member, job = members.get(alert_id), jobs.get(job_id)
                if member is None:
                    raise ValueError("alert is outside the fixed round selection")
                if job is None or job.alert_id != alert_id or job.workload_kind != "corpus_experiment":
                    raise ValueError("round requires a matching corpus processing job")
                old = existing.get(alert_id)
                if old is not None:
                    if old.job_id != job_id or old.sequence_number != sequence_number:
                        raise CorpusExperimentConflict("round item cannot be replaced")
                    continue
                session.add(SocCorpusRoundItemRow(round_id=round_id, alert_id=alert_id, group_id=member.group_id, sequence_number=sequence_number, job_id=job_id))
            session.commit()

    def get_round(self, round_id: str) -> CorpusRound | None:
        with self._session_factory() as session:
            row = session.get(SocCorpusRoundRow, round_id)
            return CorpusRound.model_validate(row.record_payload) if row else None

    def list_rounds(self, *, experiment_id: str | None = None, state: RoundState | None = None, limit: int = 100, offset: int = 0) -> list[CorpusRound]:
        _page(limit, offset)
        query = select(SocCorpusRoundRow)
        if experiment_id is not None:
            query = query.where(SocCorpusRoundRow.experiment_id == experiment_id)
        if state is not None:
            query = query.where(SocCorpusRoundRow.state == state)
        with self._session_factory() as session:
            return [CorpusRound.model_validate(row.record_payload) for row in session.scalars(query.order_by(SocCorpusRoundRow.created_at, SocCorpusRoundRow.round_id).offset(offset).limit(limit))]

    def list_round_briefs(self, experiment_id: str, *, batch: str | None = None, limit: int = 50, offset: int = 0) -> list[CorpusRoundBrief]:
        _page(limit, offset)
        row = SocCorpusRoundRow
        query = select(row.round_id, row.batch, row.state, row.created_at).where(row.experiment_id == experiment_id)
        if batch is not None:
            query = query.where(row.batch == batch)
        with self._session_factory() as session:
            return [CorpusRoundBrief.model_validate(dict(item)) for item in session.execute(query.order_by(row.created_at.desc(), row.round_id).offset(offset).limit(limit)).mappings()]

    def list_interactive_round_ids(self, *, limit: int = 100) -> list[str]:
        """Only explicitly selected single-alert work receives interactive priority."""
        _page(limit, 0)
        row, job, item = SocCorpusRoundRow, SocProcessingJobRow, SocCorpusRoundItemRow
        active = aliased(SocProcessingJobRow)
        occupied = select(active.job_id).where(active.concurrency_key == job.concurrency_key, active.status.in_([s.value for s in ACTIVE_PROCESSING_JOB_STATUSES])).exists()
        query = (
            select(row.round_id)
            .join(item, item.round_id == row.round_id)
            .join(job, job.job_id == item.job_id)
            .where(
                row.state == "running",
                row.record_payload["selection"]["alert_ids"][0].as_string().is_not(None),
                row.record_payload["selection"]["alert_ids"][1].as_string().is_(None),
                job.status == "queued",
                job.available_at <= datetime.now(UTC),
                ~occupied,
            )
            .order_by(row.created_at, row.round_id)
            .limit(limit)
        )
        with self._session_factory() as session:
            return list(session.scalars(query))

    def set_round_state(self, round_id: str, *, expected_version: int, state: RoundState, reason: str | None, execution_limit: int | None = None, concurrency: int | None = None, actor_id: str = "soc-corpus-worker") -> CorpusRound:
        with self._session_factory() as session:
            row = session.get(SocCorpusRoundRow, round_id)
            if row is None or row.version != expected_version:
                raise CorpusExperimentConflict("round changed; reload before updating")
            previous = CorpusRound.model_validate(row.record_payload)
            allowed = {"prepared": {"running", "paused", "blocked"}, "running": {"paused", "blocked", "completed"}, "paused": {"running", "blocked"}, "blocked": set(), "completed": {"running", "blocked"}}
            if state != previous.state and state not in allowed[previous.state]:
                raise CorpusExperimentConflict(f"cannot change round from {previous.state} to {state}; start a new round after snapshot changes")
            new_limit = execution_limit if execution_limit is not None else previous.execution_limit
            if new_limit < previous.execution_limit:
                raise ValueError("execution limit cannot shrink within a round")
            data = previous.model_dump()
            data.update(state=state, state_reason=reason, version=expected_version + 1, updated_at=datetime.now(UTC), execution_limit=new_limit, concurrency=concurrency if concurrency is not None else previous.concurrency)
            data["state_history"] = [
                *previous.state_history,
                {"from": previous.state, "to": state, "reason": reason, "actor_id": actor_id, "at": data["updated_at"].isoformat(), "execution_limit": new_limit, "concurrency": data["concurrency"]},
            ]
            updated = CorpusRound.model_validate(data)
            result = session.execute(
                update(SocCorpusRoundRow).where(SocCorpusRoundRow.round_id == round_id, SocCorpusRoundRow.version == expected_version).values(state=state, version=updated.version, record_payload=updated.model_dump(mode="json"))
            )
            if result.rowcount != 1:
                raise CorpusExperimentConflict("round changed concurrently")
            session.commit()
            return updated

    def list_round_items(self, round_id: str, *, limit: int = 100, offset: int = 0) -> CorpusRoundItemPage:
        _page(limit, offset)
        query = select(SocCorpusRoundItemRow, SocProcessingJobRow).join(SocProcessingJobRow, SocProcessingJobRow.job_id == SocCorpusRoundItemRow.job_id).where(SocCorpusRoundItemRow.round_id == round_id)
        with self._session_factory() as session:
            count = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.execute(query.order_by(SocCorpusRoundItemRow.sequence_number).offset(offset).limit(limit))
            return CorpusRoundItemPage(
                total=count or 0, items=[CorpusRoundItem(round_id=row.round_id, alert_id=row.alert_id, group_id=row.group_id, sequence_number=row.sequence_number, job_id=row.job_id, job=_job_from_row(job)) for row, job in rows]
            )

    def round_jobs_for_alerts(self, round_id: str, alert_ids: Sequence[str]) -> dict:
        if not alert_ids or len(alert_ids) > 500:
            raise ValueError("comparison lookup requires 1..500 alert IDs")
        query = (
            select(SocCorpusRoundItemRow.alert_id, SocProcessingJobRow)
            .join(SocProcessingJobRow, SocProcessingJobRow.job_id == SocCorpusRoundItemRow.job_id)
            .where(SocCorpusRoundItemRow.round_id == round_id, SocCorpusRoundItemRow.alert_id.in_(alert_ids))
        )
        with self._session_factory() as session:
            return {alert_id: _job_from_row(job) for alert_id, job in session.execute(query)}

    def round_progress(self, round_id: str) -> CorpusRoundProgress:
        round_ = self.get_round(round_id)
        if round_ is None:
            raise ValueError("round not found")
        base = select(SocProcessingJobRow.status).join(SocCorpusRoundItemRow, SocCorpusRoundItemRow.job_id == SocProcessingJobRow.job_id).where(SocCorpusRoundItemRow.round_id == round_id)
        with self._session_factory() as session:
            count = session.scalar(select(func.count()).select_from(base.subquery())) or 0
            counts = dict(session.execute(base.add_columns(func.count()).where(SocCorpusRoundItemRow.sequence_number < round_.execution_limit).group_by(SocProcessingJobRow.status)).all())
            return CorpusRoundProgress(
                round=round_,
                selected_count=count,
                admitted_count=sum(counts.values()),
                counts=counts,
                active_count=sum(counts.get(s.value, 0) for s in ACTIVE_PROCESSING_JOB_STATUSES),
                completed_count=counts.get("completed", 0),
                failed_count=counts.get("failed", 0),
            )

    def eligible_job_ids(self, round_id: str, *, limit: int = 100) -> list[str]:
        _page(limit, 0)
        progress = self.round_progress(round_id)
        if progress.round.state != "running" or progress.active_count >= progress.round.concurrency:
            return []
        earlier = aliased(SocCorpusRoundItemRow)
        earlier_job = aliased(SocProcessingJobRow)
        terminal = [s.value for s in ProcessingJobStatus if s.is_terminal]
        older_pending = (
            select(earlier.job_id)
            .join(earlier_job, earlier_job.job_id == earlier.job_id)
            .where(earlier.round_id == SocCorpusRoundItemRow.round_id, earlier.group_id == SocCorpusRoundItemRow.group_id, earlier.sequence_number < SocCorpusRoundItemRow.sequence_number, earlier_job.status.not_in(terminal))
            .exists()
        )
        query = (
            select(SocCorpusRoundItemRow.job_id)
            .join(SocProcessingJobRow, SocProcessingJobRow.job_id == SocCorpusRoundItemRow.job_id)
            .where(SocCorpusRoundItemRow.round_id == round_id, SocCorpusRoundItemRow.sequence_number < progress.round.execution_limit, SocProcessingJobRow.status == "queued", ~older_pending)
            .order_by(SocCorpusRoundItemRow.sequence_number)
            .limit(min(limit, progress.round.concurrency - progress.active_count))
        )
        with self._session_factory() as session:
            return list(session.scalars(query))

    def active_job_count(self) -> int:
        """Call under the shared claim transaction to fence multiple dispatchers."""
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(SocProcessingJobRow)
                    .where(SocProcessingJobRow.workload_kind.in_(["corpus_experiment", "memory_lesson_draft"]), SocProcessingJobRow.status.in_([s.value for s in ACTIVE_PROCESSING_JOB_STATUSES]))
                )
                or 0
            )


def _members_query(experiment_id: str, selection: CorpusRoundSelection):
    row = SocCorpusExperimentMemberRow
    query = select(row).where(row.experiment_id == experiment_id, row.batch == selection.batch)
    if selection.batch == "validation" and selection.scope != "all":
        query = query.where(row.validation_tier == ("main" if selection.scope == "reuse" else "supplementary"))
    for column, values in ((row.alert_id, selection.alert_ids), (row.group_id, selection.group_ids), (row.rule_code, selection.rule_codes)):
        if values:
            query = query.where(column.in_(values))
    return query


def _page(limit: int, offset: int):
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("page limit must be 1..1000 and offset nonnegative")
