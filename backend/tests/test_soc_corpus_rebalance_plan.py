from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from soc_agent.contracts.corpus_experiments import CorpusExperiment, CorpusExperimentMember, CorpusMemorySnapshotEntry
from soc_agent.contracts.corpus_rebalance import CorpusRebalanceFact, CorpusRebalancePolicy
from soc_agent.demo.corpus_rebalance_plan import build_corpus_rebalance_plan, verify_corpus_rebalance_plan


def corpus(count=8, learning=2):
    parent = CorpusExperiment(
        experiment_id="EXP-parent",
        name="original",
        plan_id="a" * 64,
        tenant_id="tenant",
        environment="dev-corpus-eval",
        source_identity={"sha256": "b" * 64},
        member_count=count,
        created_by="operator",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    members, facts = [], []
    for i in range(count):
        time = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)
        members.append(
            CorpusExperimentMember(
                alert_id=f"A{i:03}",
                payload_hash=f"{i:064x}",
                source_index=i,
                sequence_number=i + 1,
                position_in_group=i + 1,
                group_id="old-static-group",
                batch="learning" if i < learning else "validation",
                validation_tier=None if i < learning else "main",
                event_time=time,
                rule_code="R1",
                detection_key="source:R1",
                reason="original_split",
            )
        )
        facts.append(
            CorpusRebalanceFact(
                alert_id=f"A{i:03}",
                payload_hash=f"{i:064x}",
                run_id=f"RUN-{i}",
                profile_id="tenant.profile",
                profile_version="13",
                feature_schema_version="features.v11",
                pattern_key="trusted-detector-and-core-signature",
                occurrence_key=f"event-{i}",
                event_time=time,
                semantic_status="partial",
            )
        )
    return parent, members, facts


@pytest.mark.parametrize("size,holdout,learning", [(8, 2, 6), (8, 1, 7), (6, 2, 5), (40, 2, 38)])
def test_learning_first_complete_chronological_split(size, holdout, learning):
    parent, members, facts = corpus(size)
    result = build_corpus_rebalance_plan(parent, members, facts, policy=CorpusRebalancePolicy(holdout_per_pattern=holdout))
    assert result.blockers == []
    assert result.counts == {"learning": learning, "validation_main": size - learning, "validation_supplementary": 0, "total": size}
    assert [m.alert_id for m in result.members if m.batch == "learning"] == [m.alert_id for m in members[:learning]]
    assert result.moved_alert_ids == [m.alert_id for m in members[2:learning]]
    assert len(result.groups) == 1
    assert result.groups[0].independent_occurrences == size
    assert verify_corpus_rebalance_plan(result) is None
    assert all(member.batch == "learning" for member in result.members[:2])
    assert parent.plan_id == "a" * 64 and members[2].batch == "validation"


def test_static_groups_do_not_split_true_pattern_but_profile_identity_does():
    parent, members, facts = corpus(12)
    members[5].group_id = "different-navigation-group"
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert len(result.groups) == 1 and result.counts["learning"] == 10
    for i in range(6, 12):
        facts[i] = facts[i].model_copy(update={"profile_version": "14"})
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert len(result.groups) == 2
    assert result.counts["learning"] == 10
    assert {group.independent_occurrences for group in result.groups} == {6}


def test_semantic_review_that_confirms_existing_facts_is_usable():
    parent, members, facts = corpus()
    facts = [CorpusRebalanceFact.model_validate({**fact.model_dump(), "semantic_status": "unchanged"}) for fact in facts]
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert result.blockers == [] and result.counts["learning"] == 6


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5])
def test_sparse_patterns_learn_early_occurrences_and_keep_one_supplementary_holdout(size):
    parent, members, facts = corpus(size, learning=min(1, size))
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert result.counts["learning"] == max(1, size - 1)
    assert result.counts["validation_main"] == 0
    assert result.counts["validation_supplementary"] == min(1, size - 1)
    assert len(result.moved_alert_ids) == max(0, size - 2)


def test_unseen_singleton_stays_supplementary_and_cannot_be_duplicated_for_learning():
    parent, members, facts = corpus(1, learning=0)
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert result.counts["learning"] == 0 and result.counts["validation_supplementary"] == 1


def test_occurrence_duplicates_are_indivisible_and_do_not_fill_support_threshold():
    parent, members, facts = corpus(8)
    facts[7] = facts[7].model_copy(update={"occurrence_key": facts[0].occurrence_key})
    result = build_corpus_rebalance_plan(parent, members, facts)
    batches = {m.alert_id: m.batch for m in result.members}
    assert batches["A000"] == batches["A007"] == "learning"
    assert result.groups[0].independent_occurrences == 7
    assert result.groups[0].learning_occurrences >= 5
    assert not set(result.groups[0].learning_occurrence_keys).intersection(result.groups[0].validation_occurrence_keys)
    parent, members, facts = corpus(8, learning=0)
    for i in range(4, 8):
        facts[i] = facts[i].model_copy(update={"occurrence_key": facts[i - 4].occurrence_key})
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert result.counts["learning"] == 6 and result.counts["validation_supplementary"] == 2
    assert result.groups[0].learning_occurrences == 3


def test_occurrence_shared_across_patterns_blocks_execution():
    parent, members, facts = corpus()
    facts[7] = facts[7].model_copy(update={"occurrence_key": facts[0].occurrence_key, "pattern_key": "other-behavior"})
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert any("occurrence_pattern_conflict" in item for item in result.blockers)
    assert {m.batch for m in result.members if m.alert_id in {"A000", "A007"}} == {"learning"}
    assert sum(group.learning_count for group in result.groups) == result.counts["learning"]


def test_existing_late_learning_is_preserved_without_disguising_it_as_holdout():
    parent, members, facts = corpus(8, learning=0)
    members[-1].batch = "learning"
    members[-1].validation_tier = None
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert result.counts["learning"] == 8
    assert result.groups[0].warnings == ["no_holdout_after_existing_learning"]


def test_utc_time_and_alert_identity_order_the_split_not_source_or_sequence_order():
    parent, members, facts = corpus(8, learning=0)
    for index, fact in enumerate(facts):
        facts[index] = CorpusRebalanceFact.model_validate({**fact.model_dump(), "event_time": "2026-01-01T08:00:00+08:00"})
        members[index].sequence_number = 8 - index
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert sorted(member.alert_id for member in result.members if member.batch == "validation") == ["A006", "A007"]
    assert all(member.event_time == datetime(2026, 1, 1, tzinfo=UTC) for member in result.members)
    assert "learning_holdout_times_overlap" in result.groups[0].warnings


def test_unknown_duplicate_cannot_straddle_learning_and_validation():
    parent, members, facts = corpus()
    facts[-1] = facts[-1].model_copy(update={"pattern_key": None, "occurrence_key": facts[0].occurrence_key})
    result = build_corpus_rebalance_plan(parent, members, facts, policy=CorpusRebalancePolicy(missing_facts="supplementary"))
    assert result.blockers == []
    assert result.missing_facts["A000"] == ["occurrence_has_incomplete_peer"]
    assert {member.batch for member in result.members if member.alert_id in {"A000", "A007"}} == {"learning"}
    assert "A007" in result.moved_alert_ids


def test_missing_facts_are_explicit_and_never_fall_back_to_static_grouping():
    parent, members, facts = corpus()
    result = build_corpus_rebalance_plan(parent, members, facts[:-1])
    assert "A007" in result.missing_facts
    assert result.blockers
    assert result.members[-1].validation_tier == "supplementary"
    permissive = build_corpus_rebalance_plan(parent, members, facts[:-1], policy=CorpusRebalancePolicy(missing_facts="supplementary"))
    assert permissive.blockers == [] and permissive.missing_facts == result.missing_facts
    assert permissive.members[-1].group_id != members[-1].group_id


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"event_time": None}, "event_time_missing"),
        ({"pattern_key": None}, "pattern_key_missing"),
        ({"run_id": None}, "run_id_missing"),
        ({"semantic_status": "failed"}, "semantic_review_not_ready"),
        ({"semantic_status": "skipped"}, "semantic_review_not_ready"),
    ],
)
def test_incomplete_or_failed_facts_cannot_be_promoted(change, reason):
    parent, members, facts = corpus()
    facts[7] = facts[7].model_copy(update=change)
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert reason in result.missing_facts["A007"]
    assert result.members[-1].validation_tier == "supplementary"


def test_identity_binds_sources_policy_parent_memory_and_run_provenance():
    parent, members, facts = corpus()
    result = build_corpus_rebalance_plan(parent, members, facts)
    assert result == build_corpus_rebalance_plan(parent, reversed(members), reversed(facts))
    assert result.source_run_ids == sorted(fact.run_id for fact in facts)
    changed = list(facts)
    changed[0] = facts[0].model_copy(update={"run_id": "RUN-replacement"})
    assert build_corpus_rebalance_plan(parent, members, changed).plan_id != result.plan_id
    memory = CorpusMemorySnapshotEntry(memory_id="M1", version=1, content_hash="c", facets_hash="f", record_hash="r")
    assert build_corpus_rebalance_plan(parent, members, facts, inherited_memory_refs=[memory]).plan_id != result.plan_id
    changed_parent = parent.model_copy(update={"plan_id": "c" * 64})
    assert build_corpus_rebalance_plan(changed_parent, members, facts).plan_id != result.plan_id
    tampered = result.model_copy(update={"moved_alert_ids": []})
    with pytest.raises(ValueError, match="hash"):
        verify_corpus_rebalance_plan(tampered)


def test_invalid_source_inputs_are_rejected_and_fact_contract_has_no_labels():
    parent, members, facts = corpus()
    with pytest.raises(ValueError, match="duplicate alert"):
        build_corpus_rebalance_plan(parent, [*members, members[0]], facts)
    with pytest.raises(ValueError, match="duplicate fact"):
        build_corpus_rebalance_plan(parent, members, [*facts, facts[0]])
    bad = facts[0].model_copy(update={"payload_hash": "f" * 64})
    with pytest.raises(ValueError, match="payload"):
        build_corpus_rebalance_plan(parent, members, [bad, *facts[1:]])
    with pytest.raises(ValidationError):
        CorpusRebalanceFact.model_validate({**facts[0].model_dump(), "verdict": "false_positive"})
    with pytest.raises(ValidationError):
        CorpusRebalanceFact.model_validate({**facts[0].model_dump(), "event_time": "2026-01-01T00:00:00"})
