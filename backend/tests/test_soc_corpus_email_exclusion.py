"""Synthetic email exclusions preserve corpus evidence and ordinary batch membership."""

import hashlib
import json
import sqlite3
from dataclasses import asdict, replace

import pytest
from test_soc_corpus_batch_workbench import workbench as workbench
from test_soc_corpus_experiments import context, service

from soc_agent.contracts.corpus_experiments import CorpusExperimentMember, CorpusQuickCommand, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_batch_preview import prepare_batch_preview
from soc_agent.demo.corpus_batches import CorpusBatchCase, build_corpus_batch_plan
from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation
from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchError, SocCorpusWorkbenchService, _case_index_record, _load_cases_from_index
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile


@pytest.mark.parametrize("source_type", ["siem", "SIEM", "SiEm"])
def test_email_exclusion_matches_only_the_reviewed_source_and_rule(source_type):
    from soc_agent.integrations.pingan.corpus_validation import is_corpus_validation_excluded

    assert is_corpus_validation_excluded(rule_code="RPAADM_002192", source_type=source_type, topic="T_GBD_zeus_data")


@pytest.mark.parametrize(
    "changed",
    [
        {"rule_code": "RPAADM_002193"},
        {"rule_code": "rpaadm_002192"},
        {"rule_code": None},
        {"source_type": "ndr"},
        {"source_type": None},
        {"topic": "another_topic"},
        {"topic": "t_gbd_zeus_data"},
        {"topic": None},
    ],
)
def test_email_exclusion_does_not_expand_to_other_sources_or_rules(changed):
    from soc_agent.integrations.pingan.corpus_validation import is_corpus_validation_excluded

    identity = {"rule_code": "RPAADM_002192", "source_type": "siem", "topic": "T_GBD_zeus_data"}
    assert not is_corpus_validation_excluded(**{**identity, **changed})


def _batch_cases(cases):
    return [CorpusBatchCase.model_validate({key: value for key, value in _case_index_record(case).items() if key in CorpusBatchCase.model_fields}) for case in cases.values()]


def test_exclusion_preserves_remaining_members_original_split_and_position(workbench):
    cases = _batch_cases(workbench._cases)
    identity = {"source": "synthetic"}
    original = build_corpus_batch_plan(cases, source_identity=identity)
    excluded = {"0", "9", "12", "23"}
    filtered = build_corpus_batch_plan(cases, source_identity=identity, excluded_alert_ids=excluded)

    assert filtered.members == [member for member in original.members if member.alert_id not in excluded]
    assert filtered.counts == {"learning": 8, "validation_main": 9, "validation_supplementary": 3, "total": 20}
    assert filtered.plan_id != original.plan_id
    assert filtered.source_identity == original.source_identity
    assert {group.group_id for group in filtered.groups} == {"large", "small"}
    assert sum(group.total for group in filtered.groups) == filtered.counts["total"]
    large = next(group for group in filtered.groups if group.group_id == "large")
    assert (large.learning_count, large.validation_main_count, large.learning_last_id, large.validation_first_id) == (8, 9, "8", "10")
    assert build_corpus_batch_plan(cases, source_identity=identity).plan_id == original.plan_id


def test_excluding_all_members_produces_empty_plan_without_destroying_inventory(workbench):
    cases = _batch_cases(workbench._cases)
    plan = build_corpus_batch_plan(cases, source_identity={"source": "synthetic"}, excluded_alert_ids=set(workbench._cases))
    assert plan.members == []
    assert plan.groups == []
    assert plan.counts == {"learning": 0, "validation_main": 0, "validation_supplementary": 0, "total": 0}
    assert len(cases) == 24


@pytest.fixture
def email_workbench(workbench, monkeypatch):
    cases = dict(workbench._cases)
    for alert_id in ("0", "12", "23"):
        cases[alert_id] = replace(cases[alert_id], rule_code="RPAADM_002192", source_type="siem", topic="T_GBD_zeus_data", rule_name="【模型规则】可疑邮件模型")
    monkeypatch.setattr("soc_agent.demo.corpus_workbench._load_cases", lambda *args, **kwargs: cases)
    result = SocCorpusWorkbenchService(
        repository=workbench._repository,
        analysis_service=workbench._analysis_service,
        pattern_service=workbench._pattern_service,
        source_path=workbench._source_path,
        settings=workbench._settings,
        database_file=workbench._database_file,
    )
    yield result
    result._execution_executor.shutdown(wait=True)


def test_email_is_absent_from_batch_lists_groups_and_counts_but_raw_cases_remain(email_workbench):
    bench = email_workbench
    originals = {key: _case_index_record(value) for key, value in bench._cases.items()}
    expected_counts = {("learning", None): 9, ("validation", "main"): 9, ("validation", "supplementary"): 3}
    for (batch, tier), count in expected_counts.items():
        view = bench.get_state(batch=batch, validation_tier=tier, unprocessed_only=False, include_rehearsal=False, limit=500)
        assert view.alert_page.total == view.readiness.total_alert_count == view.batch_selection.selected_count == count
        assert len(view.alerts) == count
        assert not {"0", "12", "23"} & {alert.alert_id for alert in view.alerts}
        groups = bench.get_groups(batch=batch, validation_tier=tier, limit=100)
        assert sum(group.alert_count for group in groups.groups) == count
        assert all(group.group_id != "singleton" for group in groups.groups)
        hidden = bench.get_state(batch=batch, validation_tier=tier, search="RPAADM_002192", focus_alert_id="0", unprocessed_only=False)
        assert hidden.alert_page.total == 0
        assert hidden.alerts == []
    assert len(bench._cases) == 24
    assert {key: _case_index_record(value) for key, value in bench._cases.items()} == originals
    assert bench._source_path.read_bytes() == b"synthetic inventory, no pickle loading"


def test_workbench_with_only_excluded_email_keeps_data_and_returns_empty_batches(workbench, monkeypatch):
    cases = {key: replace(case, rule_code="RPAADM_002192", source_type="siem", topic="T_GBD_zeus_data") for key, case in workbench._cases.items()}
    monkeypatch.setattr("soc_agent.demo.corpus_workbench._load_cases", lambda *args, **kwargs: cases)
    bench = SocCorpusWorkbenchService(
        repository=workbench._repository,
        analysis_service=workbench._analysis_service,
        pattern_service=workbench._pattern_service,
        source_path=workbench._source_path,
        settings=workbench._settings,
        database_file=workbench._database_file,
    )
    try:
        quick = CorpusQuickValidation(service(bench._repository, []), bench.batch_plan)
        for batch in ("learning", "validation"):
            view = bench.get_state(batch=batch, unprocessed_only=False, include_rehearsal=False)
            assert view.alerts == []
            assert view.alert_page.total == view.readiness.total_alert_count == view.batch_selection.selected_count == 0
            assert view.groups == []
            groups = bench.get_groups(batch=batch)
            assert groups.groups == []
            assert groups.total == 0
            snapshot = quick.snapshot(batch)
            assert snapshot["items"] == []
            assert snapshot["total"] == snapshot["remaining"] == snapshot["active"] == snapshot["completed"] == snapshot["failed"] == 0
        assert bench._cases == cases
        assert len(bench._cases) == 24
        assert bench.batch_plan.members == []
        assert bench.batch_plan.groups == []
        assert quick.service.store.list_experiments() == []
        assert bench._source_path.read_bytes() == b"synthetic inventory, no pickle loading"
    finally:
        bench._execution_executor.shutdown(wait=True)


def test_quick_validation_totals_and_durable_jobs_exclude_email(email_workbench):
    calls = []
    svc = service(email_workbench._repository, calls)
    quick = CorpusQuickValidation(svc, email_workbench.batch_plan)
    assert quick.snapshot("learning")["total"] == 9
    assert [quick.snapshot("validation", scope)["total"] for scope in ("all", "reuse", "explore")] == [12, 9, 3]
    for batch, alert_id in (("learning", "0"), ("validation", "12"), ("validation", "23")):
        with pytest.raises(ValueError):
            quick.command(CorpusQuickCommand(batch=batch, action="run", alert_id=alert_id), context=context())
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    learning = svc.store.list_rounds()[0]
    validation = svc.create_round(CorpusRoundCreateCommand(experiment_id=learning.experiment_id, selection=CorpusRoundSelection(batch="validation", scope="all"), memory_mode="none"), context=context())
    jobs = [item for round_ in (learning, validation) for item in svc.store.list_round_items(round_.round_id, limit=100).items]
    assert {item.alert_id for item in jobs} == set(email_workbench._cases) - {"0", "12", "23"}
    assert len(jobs) == 21
    assert calls == []


@pytest.mark.parametrize("operation", ["process_alert", "start_alert", "load_experiment_payload"])
def test_email_cannot_escape_exclusion_through_direct_or_saved_member_execution(email_workbench, monkeypatch, operation):
    bench = email_workbench
    monkeypatch.setattr(bench, "_select_run_service", lambda *args: pytest.fail("excluded email must not select a model"))
    monkeypatch.setattr(bench, "_payload_for_case", lambda *args: pytest.fail("excluded email must not enter payload execution"))
    case = bench._cases["0"]
    with pytest.raises(SocCorpusWorkbenchError):
        if operation == "load_experiment_payload":
            member = CorpusExperimentMember(
                alert_id=case.alert_id, payload_hash=case.payload_hash, source_index=case.source_index, group_id=case.group_id, sequence_number=case.sequence_number, position_in_group=1, batch="learning", reason="previously saved member"
            )
            bench.load_experiment_payload(member)
        else:
            getattr(bench, operation)("0", context=context())
    assert bench.get_activity().active_count == 0


def test_indexed_web_and_preview_have_the_same_filtered_plan_and_unchanged_source_files(email_workbench, monkeypatch, tmp_path):
    bench = email_workbench
    source_path = bench._source_path
    store_path = source_path.with_suffix(".workbench-payloads.sqlite")
    index_path = source_path.with_suffix(".workbench-index.json")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    cases = [_case_index_record(case) for case in bench._cases.values()]
    with sqlite3.connect(store_path) as connection:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", [("schema_version", "soc.corpus_workbench_payload_store.v1"), ("source_sha256", source_hash), ("alert_count", "24")])
        connection.execute("CREATE TABLE payloads(alert_id TEXT, source_index INTEGER, payload_hash TEXT)")
        connection.executemany("INSERT INTO payloads VALUES (?, ?, ?)", [(case["alert_id"], case["source_index"], case["payload_hash"]) for case in cases])
    profile = asdict(PingAnSocMemoryProfile.identity)
    document = {
        "schema_version": "soc.corpus_workbench_index.v3",
        "source": {"file_name": source_path.name, "sha256": source_hash, "size_bytes": source_path.stat().st_size, "alert_count": 24},
        "payload_store": {"schema_version": "soc.corpus_workbench_payload_store.v1", "file_name": store_path.name, "sha256": hashlib.sha256(store_path.read_bytes()).hexdigest(), "size_bytes": store_path.stat().st_size},
        "memory_profile": profile,
        "cases": cases,
    }
    index_path.write_text(json.dumps(document), encoding="utf-8")
    before = {path: path.read_bytes() for path in (source_path, store_path, index_path)}
    monkeypatch.setattr("soc_agent.demo.corpus_workbench._load_cases", lambda source_path, *, source_sha256, index_path: _load_cases_from_index(index_path, source_path=source_path, source_sha256=source_sha256))
    indexed = SocCorpusWorkbenchService(
        repository=bench._repository,
        analysis_service=bench._analysis_service,
        pattern_service=bench._pattern_service,
        source_path=source_path,
        index_path=index_path,
        settings=bench._settings,
        database_file=bench._database_file,
    )
    try:
        preview = prepare_batch_preview(source_path=source_path, index_path=index_path, output_dir=tmp_path / "preview", expected_profile=profile)
        assert preview == indexed.batch_plan
        assert preview.counts == {"learning": 9, "validation_main": 9, "validation_supplementary": 3, "total": 21}
        assert set(indexed._cases) == set(bench._cases)
        assert preview.source_identity["source"]["alert_count"] == 24
        assert {path: path.read_bytes() for path in before} == before
    finally:
        indexed._execution_executor.shutdown(wait=True)
