"""Small, rebuildable list projections. Never load a Runtime JSON for an unchanged row."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from soc_agent.db.models import SocAnalysisRunRow as Run
from soc_agent.db.models import SocCorpusListProjectionRow as Item
from soc_agent.db.models import SocDecisionTransitionRow as Transition
from soc_agent.db.models import SocMemoryPatternObservationRow as Observation
from soc_agent.utils.hashing import stable_hash

EMPTY_REVISION = stable_hash([])


@dataclass(frozen=True)
class CorpusListSummary:
    alert_id: str
    group_id: str
    behavior_fingerprint: str | None
    decision_eligible: bool
    readiness: str
    workflow_state: str = "ready"
    base_label_comparison: str = "not_run"
    effective_label_comparison: str = "not_run"
    memory_hit: bool = False
    observed: bool = False
    decision_available: bool = False
    run_id: str | None = None
    aggregation_key: str | None = None
    semantic_features_applied: bool = False
    batch: str | None = None
    validation_tier: str | None = None


class SocCorpusListQueries:
    def __init__(self, session_factory: Callable[..., Session]) -> None:
        self._sessions = session_factory

    def rows(self, catalog_id: str) -> dict[str, tuple[str, CorpusListSummary]]:
        with self._sessions() as session:
            rows = session.execute(select(Item.alert_id, Item.source_revision, Item.projection_payload).where(Item.catalog_id == catalog_id))
            return {row.alert_id: (row.source_revision, CorpusListSummary(**row.projection_payload)) for row in rows}

    def insert_missing(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._sessions() as session:
            # DEV has one Workbench owner per process. A peer's identical static
            # registration is harmless; dialect upserts make that race idempotent.
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as upsert
            elif dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as upsert
            else:
                raise RuntimeError("Corpus list projections require SQLite or PostgreSQL")
            statement = upsert(Item).on_conflict_do_nothing(index_elements=["catalog_id", "alert_id"])
            session.execute(statement, rows)
            session.commit()

    def save(self, catalog_id: str, alert_id: str, revision: str, summary: CorpusListSummary) -> None:
        with self._sessions() as session:
            session.execute(update(Item).where(Item.catalog_id == catalog_id, Item.alert_id == alert_id).values(source_revision=revision, projection_payload=asdict(summary)))
            session.commit()

    def source_revisions(self, catalog_id: str, *, tenant_id: str, environment: str) -> dict[str, str]:
        """Hash small source columns, including late transitions and observations.

        Scope eligibility is still decided by the existing service against a full
        changed run. An out-of-scope run can invalidate a row, never authorize it.
        """
        parts: dict[str, list[Any]] = defaultdict(list)
        with self._sessions() as session:
            members = and_(Item.catalog_id == catalog_id, Item.alert_id == Run.alert_id, Item.input_hash == Run.input_hash)
            runs = session.execute(select(Run.alert_id, Run.run_id, Run.started_at, Run.updated_at, Run.status).join(Item, members).order_by(Run.run_id))
            for row in runs:
                parts[row.alert_id].append(["run", row.run_id, row.started_at.isoformat(), row.updated_at.isoformat(), row.status])
            transitions = session.execute(select(Run.alert_id, Transition.transition_id, Transition.created_at).join(Transition, Transition.run_id == Run.run_id).join(Item, members).order_by(Transition.transition_id))
            for row in transitions:
                parts[row.alert_id].append(["transition", row.transition_id, row.created_at.isoformat()])
            observations = session.execute(
                select(Observation.alert_id, Observation.observation_id, Observation.aggregation_key, Observation.created_at)
                .join(Item, and_(Item.catalog_id == catalog_id, Item.alert_id == Observation.alert_id))
                .where(Observation.tenant_id == tenant_id, Observation.environment == environment)
                .order_by(Observation.observation_id)
            )
            for row in observations:
                parts[row.alert_id].append(["observation", row.observation_id, row.aggregation_key, row.created_at.isoformat()])
        return {alert_id: stable_hash(values) for alert_id, values in parts.items()}

    def aggregation_counts(self) -> dict[str, int]:
        with self._sessions() as session:
            return dict(session.execute(select(Observation.aggregation_key, func.count()).group_by(Observation.aggregation_key)).all())

    def support_count(self, aggregation_key: str) -> int:
        with self._sessions() as session:
            return session.scalar(select(func.count()).select_from(Observation).where(Observation.aggregation_key == aggregation_key)) or 0

    def page(
        self,
        catalog_id: str,
        *,
        search: str | None,
        readiness: str | None,
        source_type: str | None,
        group_id: str | None,
        comparison: str | None,
        run_status: str | None = None,
        unprocessed_only: bool,
        focus_alert_id: str | None,
        active_alert_ids: list[str],
        failed_alert_ids: list[str] | None = None,
        queued_alert_ids: list[str] | None = None,
        queued_readiness: dict[str, str] | None = None,
        limit: int,
        offset: int,
        batch: str | None = None,
        validation_tier: str | None = None,
    ) -> tuple[int, list[str]]:
        item = Item
        queued = None
        if queued_alert_ids:
            # Bind a full-batch pending set once even when several filters use it.
            # Repeating 12k IDs for status/comparison/observed can exceed SQLite's
            # parameter limit. This is a read-only overlay, never a cache rewrite.
            readiness_groups: dict[str | None, list[str]] = defaultdict(list)
            for alert_id in queued_alert_ids:
                readiness_groups[(queued_readiness or {}).get(alert_id)].append(alert_id)
            current_readiness = case(*[(Item.alert_id.in_(ids), value if value is not None else Item.projection_payload["readiness"].as_string()) for value, ids in readiness_groups.items()])
            pending_rows = select(Item, current_readiness.label("queued_readiness")).where(Item.catalog_id == catalog_id).cte("current_corpus_rows")
            item = aliased(Item, pending_rows)
            queued = pending_rows.c.queued_readiness
        pending = queued.is_not(None) if queued is not None else item.alert_id.in_([])
        filters = [item.catalog_id == catalog_id]
        payload = item.projection_payload
        if batch:
            filters.append(payload["batch"].as_string() == batch)
        if validation_tier:
            filters.append(payload["validation_tier"].as_string() == validation_tier)
        focused = item.alert_id == focus_alert_id if focus_alert_id else False
        if search and search.strip():
            filters.append(item.search_text.contains(search.strip().casefold(), autoescape=True))
        if readiness:
            current_readiness = func.coalesce(queued, payload["readiness"].as_string()) if queued is not None else payload["readiness"].as_string()
            filters.append(or_(current_readiness == readiness, focused))
        if source_type:
            filters.append(item.source_type == source_type)
        if group_id:
            filters.append(item.group_id == group_id)
        if run_status:
            active = item.alert_id.in_(active_alert_ids)
            failed = item.alert_id.in_(failed_alert_ids or [])
            state = payload["workflow_state"].as_string()
            if run_status == "running":
                matching = or_(and_(state == "running", ~failed, ~pending), active)
            elif run_status == "failed":
                matching = and_(or_(state == "failed", failed), ~active, ~pending)
            elif run_status == "not_run":
                matching = and_(or_(pending, and_(state == "ready", ~failed)), ~active)
            else:
                states = ["completed", "analysis_only"]
                matching = and_(state.in_(states), ~active, ~failed, ~pending)
            filters.append(matching)
        if unprocessed_only:
            filters.append(or_(pending, payload["observed"].as_boolean().is_(False), item.alert_id.in_(active_alert_ids), focused))
        if comparison == "labeled":
            filters.append(or_(item.labeled.is_(True), focused))
        elif comparison:
            if comparison == "unlabeled":
                matching = item.labeled.is_(False)
            elif comparison == "not_run":
                matching = and_(item.labeled.is_(True), or_(pending, payload["decision_available"].as_boolean().is_(False)))
            else:
                matching = and_(~pending, item.labeled.is_(True), payload["decision_available"].as_boolean().is_(True), payload["effective_label_comparison"].as_string() == comparison)
            filters.append(or_(matching, focused))
        with self._sessions() as session:
            total = session.scalar(select(func.count()).select_from(item).where(*filters)) or 0
            ids = session.scalars(select(item.alert_id).where(*filters).order_by(item.sequence_number, item.alert_id).limit(limit).offset(offset)).all()
            return total, list(ids)
