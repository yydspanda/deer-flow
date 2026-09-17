"""Label-blind, group-wise corpus partitions; no Runtime or persistence side effects."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.utils.hashing import stable_hash

CorpusBatch = Literal["learning", "validation"]
CorpusValidationTier = Literal["main", "supplementary"]


class CorpusBatchSelection(BaseModel):
    """Read-only browsing scope, not authorization to run an experiment."""

    plan_id: str
    batch: CorpusBatch
    validation_tier: CorpusValidationTier | None = None
    counts: dict[str, int]
    selected_count: int
    group_count: int
    labeled_count: int
    first_event_time: str | None = None
    last_event_time: str | None = None
    execution_enabled: Literal[False] = False
    existing_results_only: Literal[True] = True


class CorpusBatchCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(min_length=1)
    source_index: int = Field(ge=0)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: str | None
    group_id: str = Field(min_length=1)
    window_id: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    source_type: str | None = None
    source_system: str | None = None
    product: str | None = None
    detection_key: str | None = None
    behavior_components: list[str] = Field(default_factory=list)
    behavior_fingerprint: str | None = None
    decision_eligible: bool = False


class CorpusBatchMember(CorpusBatchCase):
    batch: Literal["learning", "validation"]
    validation_tier: Literal["main", "supplementary"] | None
    reason: str
    event_time_utc: datetime | None
    position_in_group: int


class CorpusBatchGroup(BaseModel):
    group_id: str
    rule_codes: list[str]
    rule_names: list[str]
    behavior_components: list[str]
    total: int
    learning_count: int
    validation_main_count: int
    validation_supplementary_count: int
    learning_last_id: str | None
    learning_last_at: datetime | None
    validation_first_id: str | None
    validation_first_at: datetime | None
    ordinary_window_max_count: int
    boundary_same_time: bool
    warnings: list[str]


class CorpusBatchPlan(BaseModel):
    schema_version: Literal["soc.corpus_group_time_split.v2"] = "soc.corpus_group_time_split.v2"
    approval_status: Literal["draft"] = "draft"
    execution_enabled: Literal[False] = False
    grouping_basis: Literal["static_workbench_index"] = "static_workbench_index"
    split_method: Literal["per_group_time_70_30_min_five_max_ten"] = "per_group_time_70_30_min_five_max_ten"
    max_learning_per_group: Literal[10] = 10
    learning_aggregation: Literal["experiment_actual_pattern"] = "experiment_actual_pattern"
    learning_aggregation_status: Literal["planned"] = "planned"
    plan_id: str
    source_identity: dict[str, Any]
    counts: dict[str, int]
    members: list[CorpusBatchMember]
    groups: list[CorpusBatchGroup]


def _time(value: str | None) -> tuple[datetime | None, str | None]:
    if not value or not value.strip():
        return None, "event_time_missing"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            return None, "event_time_timezone_missing"
        return parsed.astimezone(UTC), None
    except (ValueError, OverflowError):
        return None, "event_time_invalid"


def build_corpus_batch_plan(cases: Iterable[CorpusBatchCase], *, source_identity: dict[str, Any]) -> CorpusBatchPlan:
    cases = list(cases)
    if not cases:
        raise ValueError("empty corpus cannot form a batch preview")
    if len({row.alert_id for row in cases}) != len(cases):
        raise ValueError("duplicate alert identity; reconcile source records before splitting")
    grouped: dict[str, list[CorpusBatchCase]] = defaultdict(list)
    for row in cases:
        grouped[row.group_id].append(row)
    members: list[CorpusBatchMember] = []
    groups: list[CorpusBatchGroup] = []
    for group_id, rows in sorted(grouped.items()):
        times = {row.alert_id: _time(row.observed_at) for row in rows}
        rows.sort(key=lambda row: (times[row.alert_id][0] or datetime.max.replace(tzinfo=UTC), row.alert_id, row.payload_hash))
        valid_count = sum(times[row.alert_id][0] is not None for row in rows)
        cut = min(10, valid_count - 1, max(5, valid_count * 7 // 10)) if valid_count >= 6 else 0
        group_members: list[CorpusBatchMember] = []
        for position, row in enumerate(rows):
            event_time, time_problem = times[row.alert_id]
            learning = position < cut
            main = bool(cut and event_time is not None and not learning)
            reason = time_problem or ("group_early_samples" if learning else "group_later_samples" if main else "singleton" if valid_count == 1 else "small_group")
            group_members.append(
                CorpusBatchMember(
                    **row.model_dump(),
                    batch="learning" if learning else "validation",
                    validation_tier=None if learning else "main" if main else "supplementary",
                    reason=reason,
                    event_time_utc=event_time,
                    position_in_group=position + 1,
                )
            )
        learning_rows = [row for row in group_members if row.batch == "learning"]
        validation_rows = [row for row in group_members if row.validation_tier == "main"]
        last = learning_rows[-1] if learning_rows else None
        first = validation_rows[0] if validation_rows else None
        window_count = max(Counter(row.window_id for row in learning_rows if row.window_id).values(), default=0)
        warnings = []
        if any(row.behavior_fingerprint is None for row in rows):
            warnings.append("fingerprint_missing")
        if any(not row.decision_eligible for row in rows):
            warnings.append("direct_reuse_not_ready")
        if valid_count != len(rows):
            warnings.append("event_time_unusable")
        groups.append(
            CorpusBatchGroup(
                group_id=group_id,
                rule_codes=sorted({row.rule_code for row in rows if row.rule_code}),
                rule_names=sorted({row.rule_name for row in rows if row.rule_name}),
                behavior_components=sorted({component for row in rows for component in row.behavior_components}),
                total=len(rows),
                learning_count=cut,
                validation_main_count=len(validation_rows),
                validation_supplementary_count=len(rows) - cut - len(validation_rows),
                learning_last_id=last.alert_id if last else None,
                learning_last_at=last.event_time_utc if last else None,
                validation_first_id=first.alert_id if first else None,
                validation_first_at=first.event_time_utc if first else None,
                ordinary_window_max_count=window_count,
                boundary_same_time=bool(last and first and last.event_time_utc == first.event_time_utc),
                warnings=warnings,
            )
        )
        members.extend(group_members)
    counts = {
        "learning": sum(m.batch == "learning" for m in members),
        "validation_main": sum(m.validation_tier == "main" for m in members),
        "validation_supplementary": sum(m.validation_tier == "supplementary" for m in members),
        "total": len(members),
    }
    plan = CorpusBatchPlan(plan_id="", source_identity=source_identity, counts=counts, members=members, groups=groups)
    return plan.model_copy(update={"plan_id": stable_hash(plan.model_dump(mode="json", exclude={"plan_id"}))})
