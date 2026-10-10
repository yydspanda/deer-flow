"""Pure successor-manifest planning from saved semantic facts, with no I/O."""

from collections import defaultdict
from collections.abc import Iterable

from soc_agent.contracts.corpus_experiments import CorpusExperiment, CorpusExperimentMember, CorpusMemorySnapshotEntry
from soc_agent.contracts.corpus_rebalance import CorpusRebalanceFact, CorpusRebalanceGroup, CorpusRebalancePlan, CorpusRebalancePolicy
from soc_agent.utils.hashing import stable_hash


def corpus_rebalance_members_hash(members: Iterable[CorpusExperimentMember]) -> str:
    return stable_hash([member.model_dump(mode="json") for member in sorted(members, key=lambda item: item.alert_id)])


def _pattern_id(fact: CorpusRebalanceFact) -> str:
    return "CRG-" + stable_hash([fact.profile_id, fact.profile_version, fact.feature_schema_version, fact.pattern_key])


def _problems(fact: CorpusRebalanceFact | None) -> list[str]:
    if fact is None:
        return ["semantic_facts_missing"]
    problems = [f"{field}_missing" for field in ("run_id", "profile_id", "profile_version", "feature_schema_version", "pattern_key", "occurrence_key", "event_time") if not getattr(fact, field)]
    if fact.semantic_status not in {"applied", "partial", "unchanged"}:
        problems.append("semantic_review_not_ready")
    return problems


def build_corpus_rebalance_plan(
    parent_experiment: CorpusExperiment,
    members: Iterable[CorpusExperimentMember],
    facts: Iterable[CorpusRebalanceFact],
    *,
    policy: CorpusRebalancePolicy | None = None,
    inherited_memory_refs: Iterable[CorpusMemorySnapshotEntry] = (),
) -> CorpusRebalancePlan:
    """Preserve prior learning and assign later independent occurrences as holdouts.

    Existing learning is never returned to validation. An occurrence already used
    for learning protects all of its duplicate alerts. Missing facts are explicit;
    they never inherit the static index's behavior identity. ``plan_id`` binds all
    evidence and provenance; application code must re-resolve them before apply.
    """
    policy = policy or CorpusRebalancePolicy()
    members = sorted(members, key=lambda item: item.alert_id)
    facts = sorted(facts, key=lambda item: item.alert_id)
    if not members:
        raise ValueError("empty corpus cannot form a successor plan")
    by_id = {member.alert_id: member for member in members}
    fact_by_id = {fact.alert_id: fact for fact in facts}
    if len(by_id) != len(members):
        raise ValueError("duplicate alert identity in parent manifest")
    if len(fact_by_id) != len(facts):
        raise ValueError("duplicate fact identity; select one saved fact per alert explicitly")
    if set(fact_by_id) - set(by_id):
        raise ValueError("fact alert is absent from parent manifest")
    if parent_experiment.member_count != len(members):
        raise ValueError("parent member count does not match its manifest")
    for fact in facts:
        if fact.payload_hash != by_id[fact.alert_id].payload_hash:
            raise ValueError("fact payload hash differs from parent manifest")
    missing = {member.alert_id: problems for member in members if (problems := _problems(fact_by_id.get(member.alert_id)))}
    occurrences: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        if fact.occurrence_key:
            occurrences[fact.occurrence_key].append(fact.alert_id)
    blockers = []
    protected = {member.alert_id for member in members if member.batch == "learning"}
    for occurrence, ids in occurrences.items():
        if protected.intersection(ids):
            protected.update(ids)
        patterns = {_pattern_id(fact_by_id[alert_id]) for alert_id in ids if alert_id not in missing}
        if len(patterns) > 1:
            blockers.append(f"occurrence_pattern_conflict:{occurrence}")
        # A partially identified duplicate cannot become a holdout of its own
        # learning twin merely because one fact record is incomplete.
        if any(alert_id in missing for alert_id in ids):
            for alert_id in ids:
                if alert_id not in missing:
                    missing[alert_id] = ["occurrence_has_incomplete_peer"]
    if missing and policy.missing_facts == "block":
        blockers.append(f"semantic_facts_incomplete:{len(missing)}")
    output = {}
    grouped: dict[str, list[CorpusRebalanceFact]] = defaultdict(list)
    for member in members:
        fact = fact_by_id.get(member.alert_id)
        learning = member.alert_id in protected
        output[member.alert_id] = member.model_copy(
            update={
                "batch": "learning" if learning else "validation",
                "validation_tier": None if learning else "supplementary",
                "group_id": _pattern_id(fact) if member.alert_id not in missing else "CRU-" + stable_hash([member.alert_id, member.payload_hash]),
                "reason": "original_learning_preserved" if member.batch == "learning" else "learning_occurrence_duplicate" if learning else "semantic_facts_incomplete",
                "event_time": fact.event_time if fact and fact.event_time is not None else member.event_time,
            }
        )
        if member.alert_id not in missing:
            grouped[_pattern_id(fact)].append(fact)
    group_metadata = []
    for group_id, rows in sorted(grouped.items()):
        by_occurrence: dict[str, list[CorpusRebalanceFact]] = defaultdict(list)
        for fact in rows:
            by_occurrence[fact.occurrence_key].append(fact)
        units = sorted(by_occurrence.values(), key=lambda unit: (min(fact.event_time for fact in unit), min(fact.alert_id for fact in unit)))
        protected_positions = [index for index, unit in enumerate(units) if any(fact.alert_id in protected for fact in unit)]
        cut = 0
        warnings = []
        if len(units) >= policy.minimum_learning_occurrences + 1:
            holdout = min(policy.holdout_per_pattern, len(units) - policy.minimum_learning_occurrences)
            cut = max(len(units) - holdout, max(protected_positions, default=-1) + 1)
            if cut == len(units):
                warnings.append("no_holdout_after_existing_learning")
        elif len(units) > 1:
            cut = max(len(units) - 1, max(protected_positions, default=-1) + 1)
            warnings.append("insufficient_independent_occurrences")
            if cut == len(units):
                warnings.append("no_holdout_after_existing_learning")
        main_holdout = cut >= policy.minimum_learning_occurrences
        for position, unit in enumerate(units):
            learning = position < cut or any(fact.alert_id in protected for fact in unit)
            for fact in unit:
                old = by_id[fact.alert_id]
                output[fact.alert_id] = output[fact.alert_id].model_copy(
                    update={
                        "batch": "learning" if learning else "validation",
                        "validation_tier": None if learning else "main" if main_holdout else "supplementary",
                        "reason": "original_learning_preserved"
                        if old.batch == "learning"
                        else "learning_occurrence_duplicate"
                        if fact.alert_id in protected
                        else "pattern_early_samples"
                        if learning
                        else "pattern_later_samples"
                        if main_holdout
                        else "insufficient_independent_occurrences",
                        "position_in_group": position + 1,
                    }
                )
        group_metadata.append((group_id, rows, units, warnings))
    # Conflicting profiles already block apply; still keep the preview's duplicate
    # occurrences together, including duplicates of original learning samples.
    for ids in occurrences.values():
        if any(output[alert_id].batch == "learning" for alert_id in ids):
            for alert_id in ids:
                output[alert_id] = output[alert_id].model_copy(update={"batch": "learning", "validation_tier": None})
    groups = []
    for group_id, rows, units, warnings in group_metadata:
        learning_rows = [output[fact.alert_id] for fact in rows if output[fact.alert_id].batch == "learning"]
        validation_rows = [output[fact.alert_id] for fact in rows if output[fact.alert_id].batch == "validation"]
        if learning_rows and validation_rows and max(row.event_time for row in learning_rows) >= min(row.event_time for row in validation_rows):
            warnings.append("learning_holdout_times_overlap")
        first = rows[0]
        groups.append(
            CorpusRebalanceGroup(
                group_id=group_id,
                pattern_key=first.pattern_key,
                profile_id=first.profile_id,
                profile_version=first.profile_version,
                feature_schema_version=first.feature_schema_version,
                member_count=len(rows),
                independent_occurrences=len(units),
                learning_occurrences=len({fact.occurrence_key for fact in rows if output[fact.alert_id].batch == "learning"}),
                validation_occurrences=len({fact.occurrence_key for fact in rows if output[fact.alert_id].batch == "validation"}),
                learning_occurrence_keys=sorted({fact.occurrence_key for fact in rows if output[fact.alert_id].batch == "learning"}),
                validation_occurrence_keys=sorted({fact.occurrence_key for fact in rows if output[fact.alert_id].batch == "validation"}),
                learning_count=len(learning_rows),
                validation_main_count=sum(row.validation_tier == "main" for row in validation_rows),
                validation_supplementary_count=sum(row.validation_tier == "supplementary" for row in validation_rows),
                warnings=warnings,
            )
        )
    new_members = sorted(output.values(), key=lambda item: (item.sequence_number, item.alert_id))
    refs = sorted(inherited_memory_refs, key=lambda ref: (ref.memory_id, ref.version))
    if len({(ref.memory_id, ref.version) for ref in refs}) != len(refs):
        raise ValueError("duplicate inherited memory reference")
    fact_values = [fact.model_dump(mode="json") for fact in facts]
    value = {
        "schema_version": "soc.corpus_rebalance_plan.v1",
        "parent_experiment_id": parent_experiment.experiment_id,
        "parent_plan_id": parent_experiment.plan_id,
        "parent_members_hash": corpus_rebalance_members_hash(members),
        "tenant_id": parent_experiment.tenant_id,
        "environment": parent_experiment.environment,
        "source_identity": parent_experiment.source_identity,
        "policy": policy.model_dump(mode="json"),
        "facts": fact_values,
        "facts_hash": stable_hash(fact_values),
        "source_run_ids": sorted({fact.run_id for fact in facts if fact.run_id}),
        "inherited_memory_refs": [ref.model_dump(mode="json") for ref in refs],
        "members": [member.model_dump(mode="json") for member in new_members],
        "counts": {
            "learning": sum(m.batch == "learning" for m in new_members),
            "validation_main": sum(m.validation_tier == "main" for m in new_members),
            "validation_supplementary": sum(m.validation_tier == "supplementary" for m in new_members),
            "total": len(new_members),
        },
        "moved_alert_ids": sorted(member.alert_id for member in new_members if member.batch == "learning" and by_id[member.alert_id].batch == "validation"),
        "groups": [group.model_dump(mode="json") for group in groups],
        "missing_facts": dict(sorted(missing.items())),
        "blockers": sorted(blockers),
    }
    return CorpusRebalancePlan.model_validate({**value, "plan_id": stable_hash(value)})


def verify_corpus_rebalance_plan(plan: CorpusRebalancePlan) -> None:
    """Detect edited artifacts; this does not replace server-side source resolution."""
    if stable_hash(plan.model_dump(mode="json", exclude={"plan_id"})) != plan.plan_id:
        raise ValueError("rebalance plan hash mismatch")
    if stable_hash([fact.model_dump(mode="json") for fact in plan.facts]) != plan.facts_hash:
        raise ValueError("rebalance fact hash mismatch")
