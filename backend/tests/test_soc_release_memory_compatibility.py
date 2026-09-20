"""Upgrade acceptance against immutable synthetic records made by the old release."""

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from test_soc_corpus_experiments import context
from test_soc_corpus_workbench import _repository

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import AnalysisRun, LLMAnalysisRequest, MemoryPatternObservation, SocMemoryCandidate, SocMemoryRecord
from soc_agent.core import SocMemoryPatternService, SocMemoryService
from soc_agent.core.direct_resolution import SocDirectResolutionService
from soc_agent.db.models import SocAnalysisRunRow, SocMemoryCandidateRow, SocMemoryPatternObservationRow, SocMemoryRecordRow
from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchService, _CorpusCase
from soc_agent.llm import SocAnalyzerMode, SocLLMSettings
from soc_agent.memory import memory_query_from_analysis_request

FIXTURE_PATH = Path(__file__).parent / "fixtures/soc_memory/profile7_v5_release_20260918.json"
SOURCE_COMMIT = "2b4d3e43109acb75eb88ed9eb69353760d8facb1"
PAYLOAD_SHA256 = "6182bed9863490fe6290559ab37ef4a74f959dc1c83a192dad18294f56f25fb2"


@pytest.fixture
def baseline(monkeypatch):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_frozen_baseline_integrity(baseline):
    assert baseline["fixture_id"] == "pingan-profile7-v5-release-20260918"
    assert baseline["source_commit"] == SOURCE_COMMIT
    assert baseline["synthetic"] is True
    assert baseline["real_model_calls"] == 0
    encoded = json.dumps(baseline["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == baseline["payload_sha256"] == PAYLOAD_SHA256
    assert all(r["llm_analysis_request"]["memory_profile"]["profile_version"] == "7" and r["normalization_assistance"]["mode"] == "apply" for r in baseline["payload"]["runs"])
    assert all(o["profile_version"] == "7" for o in baseline["payload"]["observations"])


_STORED_MODELS = (
    ("runs", AnalysisRun, "save_run", SocAnalysisRunRow, "run_id", "run_payload"),
    ("observations", MemoryPatternObservation, "save_memory_pattern_observation", SocMemoryPatternObservationRow, "observation_id", "observation_payload"),
    ("candidates", SocMemoryCandidate, "save_memory_candidate", SocMemoryCandidateRow, "candidate_id", "candidate_payload"),
    ("records", SocMemoryRecord, "save_memory_record", SocMemoryRecordRow, "memory_id", "record_payload"),
)


def _load_old_storage(repository, payload):
    for key, model, save, row, id_column, payload_column in _STORED_MODELS:
        for original in payload[key]:
            getattr(repository, save)(model.model_validate(original))
            # Keep the exact pre-upgrade JSON, including absent newer optional fields.
            # Current schemas supply indexed columns only; they do not regenerate data.
            with repository._session_factory() as session:
                session.execute(update(row).where(getattr(row, id_column) == original[id_column]).values({payload_column: copy.deepcopy(original)}))
                session.commit()


def _stored_payloads(repository):
    with repository._session_factory() as session:
        return {key: dict(session.execute(select(getattr(row, id_column), getattr(row, payload_column))).all()) for key, _, _, row, id_column, payload_column in _STORED_MODELS}


def _old_case(run, observation, index):
    request = run["llm_analysis_request"]
    facets = observation["signature"]["facets"]
    at = datetime.fromisoformat(run["input_payload"]["event_time"])
    return _CorpusCase(
        alert_id=run["alert_id"],
        source_index=index,
        sequence_number=index + 1,
        payload=None,
        payload_hash=run["input_hash"],
        observed_at=at.isoformat(),
        observed_at_value=at,
        topic="synthetic-legacy-release",
        source_type=request["source"]["source_type"],
        source_system=request["source"]["source_system"],
        product=request["source"]["product"],
        detection_key=request["detection"]["detection_key"],
        rule_code=None,
        rule_name=request["detection"]["rule_name"],
        category=request["classification"]["category"],
        severity=request["classification"]["severity"],
        endpoint=None,
        host_name=request["canonical_entities"]["host"]["host_name"],
        process_names=(),
        behavior_fingerprint=facets["behavior_fingerprint"][0],
        behavior_components=tuple(facets["behavior_component"]),
        behavior_strength="strong",
        decision_eligible=True,
        group_id="synthetic-frozen-profile7",
        window_id="synthetic-frozen-window",
        window_start=observation["window_start"],
        window_end=observation["window_end"],
        group_alert_count=6,
        window_alert_count=6,
        readiness="candidate_window",
    )


def test_frozen_history_survives_upgrade(baseline, tmp_path, monkeypatch):
    payload = baseline["payload"]
    repository = _repository(tmp_path)
    _load_old_storage(repository, payload)
    before = _stored_payloads(repository)
    for key, _, _, _, id_column, _ in _STORED_MODELS:
        assert before[key] == {original[id_column]: original for original in payload[key]}
    observations = {o["source"]["run_id"]: o for o in payload["observations"]}
    cases = {r["alert_id"]: _old_case(r, observations[r["run_id"]], i) for i, r in enumerate(payload["runs"])}
    last = list(cases.values())[-1]
    at = last.observed_at_value + timedelta(minutes=1)
    future = replace(last, alert_id="PA-ALERT-006", source_index=5, sequence_number=6, observed_at=at.isoformat(), observed_at_value=at, payload_hash="6" * 64)
    cases[future.alert_id] = future
    source = tmp_path / "synthetic-frozen-corpus.pkl"
    source.write_bytes(b"Synthetic inventory only; no pickle deserialization or private corpus.")
    monkeypatch.setattr("soc_agent.demo.corpus_workbench._load_cases", lambda *a, **kw: cases)
    patterns = SocMemoryPatternService(repository=repository, candidate_repository=repository, profile_registry=build_soc_memory_profile_registry())
    workbench = SocCorpusWorkbenchService(repository=repository, analysis_service=SimpleNamespace(), pattern_service=patterns, source_path=source, settings=SocLLMSettings(mode=SocAnalyzerMode.STUB), database_file="temporary-fixture.sqlite")

    state = workbench.get_state(batch="learning", unprocessed_only=False, include_group_catalog=False, include_rehearsal=False)
    assert len(state.alerts) == len(payload["runs"])
    for original in payload["runs"]:
        row = next(a for a in state.alerts if a.alert_id == original["alert_id"])
        observation = observations[original["run_id"]]
        assert row.workflow_state == "completed"
        assert row.observation_id == observation["observation_id"]
        assert row.behavior_fingerprint == observation["signature"]["facets"]["behavior_fingerprint"][0]
        execution = workbench.get_execution(original["alert_id"])
        audit = workbench.get_audit_bundle(original["alert_id"], context=context(), run_id=original["run_id"])
        for view in (execution, audit.execution):
            assert view.status == "completed"
            assert view.observation_id == observation["observation_id"]
            assert next(p for p in view.phases if p.phase == "memory").status == "success"
        artifact = next(a for a in audit.artifacts if a.artifact_id == "memory-pattern-write")
        assert artifact.payload["pattern_observation"]["observation_id"] == observation["observation_id"]
    assert _stored_payloads(repository) == before


def _validation_services(baseline, tmp_path, *, boundary=None):
    repository = _repository(tmp_path)
    _load_old_storage(repository, baseline["payload"])
    record = repository.get_memory_record(baseline["payload"]["records"][0]["memory_id"])
    assert record is not None
    if boundary == "disabled":
        record.retrieval_enabled = False
        repository.save_memory_record(record)
    registry = build_soc_memory_profile_registry()
    now = datetime.fromisoformat(baseline["clock"]) + timedelta(hours=1)
    if boundary == "review_expired":
        now += timedelta(days=31)
    service = SocMemoryService(record_repository=repository, profile_registry=registry, now_provider=lambda: now, retrieval_record_ids=frozenset() if boundary == "not_allowlisted" else frozenset({record.memory_id}))
    return repository, record, registry, service


def _resolve(request, service, registry):
    query = memory_query_from_analysis_request(request, profile=registry.resolve_request(request))
    retrieved = service.find_directive_records(query)
    run = AnalysisRun(alert_id=request.alert_id, status="pending", input_payload={"synthetic": True}, llm_analysis_request=request)
    resolution = SocDirectResolutionService(memory_service=service, profile_registry=registry, environment=request.environment).resolve_memory(run)
    return retrieved, resolution


@pytest.mark.parametrize("case", ["exact", "allowed_host_change"])
def test_frozen_reviewed_memory_still_reuses_in_validation(baseline, tmp_path, case):
    repository, record, registry, service = _validation_services(baseline, tmp_path)
    before = _stored_payloads(repository)
    request = LLMAnalysisRequest.model_validate(baseline["payload"]["queries"][case])

    retrieved, resolution = _resolve(request, service, registry)

    assert [m.memory_id for m in retrieved.matches] == [record.memory_id]
    assert retrieved.matches[0].applicability_report.status.value == "applicable"
    assert resolution is not None
    assert resolution.source_kind == "memory"
    assert resolution.source_id == record.memory_id
    assert resolution.source_version == str(record.version)
    assert resolution.source_hash == record.content_hash
    assert resolution.decision.verdict.value == "false_positive"
    assert _stored_payloads(repository) == before


@pytest.mark.parametrize("boundary", ["changed_behavior", "changed_entity", "detector", "tenant", "environment", "profile", "disabled", "not_allowlisted", "review_expired"])
def test_frozen_reviewed_memory_rejects_out_of_scope(baseline, tmp_path, boundary):
    repository, record, registry, service = _validation_services(baseline, tmp_path, boundary=boundary)
    before = _stored_payloads(repository)
    request = LLMAnalysisRequest.model_validate(baseline["payload"]["queries"].get(boundary, baseline["payload"]["queries"]["exact"]))
    if boundary == "detector":
        request.detection.detection_key = "synthetic:different-rule"
    elif boundary == "tenant":
        request.tenant_id = "synthetic-other-tenant"
    elif boundary == "environment":
        request.environment = "stg"
    elif boundary == "profile":
        request.memory_profile = {"profile_id": "pingan.soc", "profile_version": "9", "feature_schema_version": "pingan.soc.memory_features.v7"}

    retrieved, resolution = _resolve(request, service, registry)

    assert not any(m.applicability_report and m.applicability_report.status.value == "applicable" for m in retrieved.matches)
    assert resolution is None
    assert _stored_payloads(repository) == before
