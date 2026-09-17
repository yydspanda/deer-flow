from __future__ import annotations

import json

import httpx
import pytest

from soc_agent.contracts import AlertInput, AnalysisRun
from soc_agent.core.runtime import SocRuntimeLifecycleError, analyze_alert
from soc_agent.llm.analyzer import LLMChatResponse
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer


class Client:
    def __init__(self, facts=(), *, error=None, content=None):
        self.calls = []
        self.facts = list(facts)
        self.error = error
        self.content = content

    def complete(self, messages, *, model_name):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return LLMChatResponse(content=self.content or json.dumps({"facts": self.facts, "unresolved": []}), model_name=model_name)


def alert(text='host="example" file="D:\\tools\\yak.exe"'):
    return AlertInput(
        alert_id="example-alert",
        raw={"message": text, "excluded": "private sibling"},
        extensions={"evidence_input_policy": {"name": "raw_message_first", "selected_input_path": "message", "selected_layer": "raw_message", "trust_level": "high"}},
    )


def fact(target="entities.file.file_path", value=r"D:\tools\yak.exe", quote='file="D:\\tools\\yak.exe"', reason="原文明确给出检测文件。"):
    return {"target": target, "value": value, "source_quote": quote, "reason": reason}


def payload(source):
    return {**source.model_dump(mode="json"), "message": source.raw["message"]}


def test_normal_input_is_reviewed_without_a_parser_warning():
    client = Client()
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")
    original = alert()
    request = reviewer.prepare(original)
    result = reviewer.review(original, request)
    assert len(client.calls) == 1
    assert result.status == "unchanged"
    assert "private sibling" not in str(client.calls)
    assert result.metadata["usage_measurement"]["status"] == "estimated"
    assert result.metadata["usage"]["total_tokens"] > 0


def test_legacy_scalar_output_also_respects_disabled_quote_validation():
    reviewer = JsonLLMNormalizationReviewer(client=Client([fact(quote="normalized source excerpt")]), model_name="test")
    run = analyze_alert(payload(alert()), normalization_reviewer=reviewer)
    assert run.failure is None
    change = run.normalization_assistance.changes[0]
    assert change.source_start is None and change.source_end is None
    assert change.reference_validation_status == "not_checked"
    assert run.normalized_alert.entities.file.file_path == r"D:\tools\yak.exe"
    assert run.normalization_assistance.metadata["reference_validation_enabled"] is False


def test_historical_frozen_request_preserves_strict_reference_semantics():
    from soc_agent.contracts import NormalizationAssistRequest

    assert NormalizationAssistRequest(alert_id="old", source={}, detection={}, configuration_hash="old").reference_validation_enabled is True


def test_added_file_reaches_runtime_entities_and_analysis_input():
    client = Client([fact()])
    source = alert()
    before = payload(source)
    run = analyze_alert(before, normalization_reviewer=JsonLLMNormalizationReviewer(client=client, model_name="test"))
    assert payload(source) == before
    assert run.normalized_alert.raw == before
    assert run.normalized_alert.entities.file.file_path == r"D:\tools\yak.exe"
    assert run.normalized_alert.entities.file.observations[0].relation.value == "observed_artifact"
    assert "yak.exe" in run.llm_analysis_request.model_dump_json()
    assert [s.step_name for s in run.steps].index("normalization_assist") < [s.step_name for s in run.steps].index("entity_extract")
    report = run.normalization_assistance
    assert report.status == "applied"
    assert report.changes[0].model_input_status == "present"
    assert report.changes[0].matching_status == "not_assessed"
    assert AnalysisRun.model_validate_json(run.model_dump_json()).normalization_assistance == report


def test_shadow_records_changes_without_mutating_actual_input():
    reviewer = JsonLLMNormalizationReviewer(client=Client([fact()]), model_name="test", mode="shadow")
    run = analyze_alert(payload(alert()), normalization_reviewer=reviewer)
    assert run.normalization_assistance.status == "shadow"
    assert run.normalized_alert.entities.file.file_path is None
    assert run.normalization_assistance.changes[0].model_input_status == "not_assessed"


def test_nonempty_truncated_value_can_be_corrected_with_lineage():
    source = alert('cmd="tool.exe --login -i"')
    source.entities.process.command_line = "tool.exe"
    client = Client([fact("entities.process.command_line", "tool.exe --login -i", 'cmd="tool.exe --login -i"', "原值遗漏参数。")])
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")
    result = reviewer.review(source, reviewer.prepare(source))
    assert result.changes[0].before == "tool.exe"
    assert result.changes[0].after == "tool.exe --login -i"
    assert source.entities.process.command_line == "tool.exe"


@pytest.mark.parametrize("proposal", [fact(value="invented.exe"), fact(target="classification.severity"), fact(quote="not in raw"), fact(value="<ENCODED:fake:OMITTED>")])
def test_invalid_fact_is_isolated(proposal):
    reviewer = JsonLLMNormalizationReviewer(client=Client([proposal, fact("entities.host.host_name", "example", 'host="example"')]), model_name="test", reference_validation_enabled=True)
    source = alert()
    result = reviewer.review(source, reviewer.prepare(source))
    assert result.status == "partial"
    assert len(result.changes) == 1
    assert result.issues


def test_ambiguous_singleton_does_not_select_the_last_host():
    source = alert('host="first" host="second"')
    client = Client([fact("entities.host.host_name", name, f'host="{name}"') for name in ("first", "second")])
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")
    result = reviewer.review(source, reviewer.prepare(source))
    assert not result.changes
    assert result.status == "partial"


@pytest.mark.parametrize("client", [Client(error=TimeoutError("secret response")), Client(content="broken JSON")])
def test_auxiliary_failure_does_not_erase_base(client):
    run = analyze_alert(payload(alert()), normalization_reviewer=JsonLLMNormalizationReviewer(client=client, model_name="test"))
    assert run.analysis is not None
    assert run.normalization_assistance.status == "failed"
    step = next(s for s in run.steps if s.step_name == "normalization_assist")
    assert step.status.value == "failed"
    assert "secret response" not in run.model_dump_json()


@pytest.mark.parametrize("failure", ["connection", "timeout", "429", "502", "invalid_json", "empty", "invalid_envelope"])
def test_review_failure_matrix_preserves_old_path(failure):
    request = httpx.Request("POST", "https://example.invalid/chat")
    errors = {
        "connection": httpx.ConnectError("private upstream detail", request=request),
        "timeout": httpx.ReadTimeout("private upstream detail", request=request),
        "429": httpx.HTTPStatusError("private upstream detail", request=request, response=httpx.Response(429, request=request)),
        "502": httpx.HTTPStatusError("private upstream detail", request=request, response=httpx.Response(502, request=request)),
    }
    client = Client(error=errors.get(failure), content={"invalid_json": "{broken", "empty": " ", "invalid_envelope": "[]"}.get(failure))
    source = alert()
    source.detection.rule_name = "approved scanner synthetic fixture"
    original = payload(source)
    baseline = analyze_alert(original)
    assert baseline.analysis.verdict.value == "false_positive"
    run = analyze_alert(original, normalization_reviewer=JsonLLMNormalizationReviewer(client=client, model_name="test"))
    assert run.failure is None
    assert run.analysis == baseline.analysis
    assert run.decision == baseline.decision
    assert run.normalized_alert == baseline.normalized_alert
    assert run.normalization_assistance.status == "failed"
    assert not run.normalization_assistance.changes
    assert len(client.calls) == 1
    assert "private upstream detail" not in run.model_dump_json()


@pytest.mark.parametrize("stage", ["build_normalization_input", "normalization_assist", "apply_normalization"])
def test_auxiliary_implementation_fault_is_isolated(stage, monkeypatch):
    client = Client([fact()])
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")

    def broken(*args, **kwargs):
        raise RuntimeError("private implementation detail")

    if stage == "apply_normalization":
        monkeypatch.setattr("soc_agent.core.runtime.apply_normalization_changes", broken)
    else:
        monkeypatch.setattr(reviewer, "prepare" if stage == "build_normalization_input" else "review", broken)
    original = payload(alert())
    baseline = analyze_alert(original)
    run = analyze_alert(original, normalization_reviewer=reviewer)
    assert run.analysis == baseline.analysis
    assert run.decision == baseline.decision
    assert run.normalized_alert == baseline.normalized_alert
    assert run.failure is None
    assert run.normalization_assistance.status == "failed"
    assert run.normalization_assistance.metadata["failure_stage"] == stage
    assert not run.normalization_assistance.changes
    assert "private implementation detail" not in run.model_dump_json()
    if stage == "apply_normalization":
        assert run.normalization_assistance.metadata["unapplied_changes"][0]["target"] == "entities.file.file_path"


def test_primary_model_failure_is_not_hidden_by_auxiliary_fallback():
    from soc_agent.pipeline.analyzer import StubLLMAnalyzer

    class UnavailableAnalyzer(StubLLMAnalyzer):
        step_name = "analyze_llm"

        def analyze(self, request):
            raise TimeoutError("primary unavailable")

    run = analyze_alert(payload(alert()), analyzer=UnavailableAnalyzer(), normalization_reviewer=JsonLLMNormalizationReviewer(client=Client(error=TimeoutError()), model_name="test"))
    assert run.normalization_assistance.status == "failed"
    assert run.status.value == "failed"
    assert run.failure.step_name == "analyze_llm"
    assert run.failure.retryable is True
    assert run.analysis is None


def test_empty_input_skips_call_and_old_runs_remain_compatible():
    client = Client()
    run = analyze_alert(payload(alert("")), normalization_reviewer=JsonLLMNormalizationReviewer(client=client, model_name="test"))
    assert not client.calls
    assert run.normalization_assistance.status == "skipped"
    old = run.model_dump(mode="json")
    del old["normalization_assistance"]
    assert AnalysisRun.model_validate(old).normalization_assistance is None


def test_journal_failure_happens_before_model_call():
    client = Client()

    def fail(*args):
        raise RuntimeError("storage unavailable")

    with pytest.raises(SocRuntimeLifecycleError):
        analyze_alert(payload(alert()), normalization_reviewer=JsonLLMNormalizationReviewer(client=client, model_name="test"), before_provider=fail)
    assert not client.calls


def test_journals_keep_the_distinct_request_and_failed_auxiliary_status():
    from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService

    class Repository:
        def __init__(self):
            self.saved = []

        def save_run(self, run):
            self.saved.append(run.model_copy(deep=True))

    repo = Repository()
    client = Client(error=TimeoutError("offline simulation"))
    service = SocAnalysisService(repository=repo, runtime=DeterministicAnalysisRuntime(normalization_reviewer=JsonLLMNormalizationReviewer(client=client, model_name="test")))
    run = service.analyze(payload(alert()))
    assert len(client.calls) == 1
    assert repo.saved[0].normalization_assist_request.source_text
    assert repo.saved[0].normalization_assistance is None
    journal = run.provider_request_journals[0]
    assert journal.request_schema_version == "soc.normalization_assist_request.v1"
    assert journal.provider_purpose.value == "normalization_assist"
    assert journal.status.value == "failed"
    assert run.provider_request_journals[-1].status.value == "completed"


def test_multiple_messages_are_separate_review_sources_within_budget():
    source = alert()
    source.extensions["evidence_input_policy"]["supplementary_input_paths"] = ["other.message"]
    source.raw["other"] = {"message": "do not merge another process"}
    reviewer = JsonLLMNormalizationReviewer(client=Client(), model_name="test")
    request = reviewer.prepare(source)
    assert "do not merge" not in request.source_text
    assert request.omitted_source_count == 0
    assert request.sources[1].source_path == "other.message"
    assert request.sources[1].text == "do not merge another process"
    assert reviewer.review(source, request).status == "unchanged"


def test_configuration_is_explicit_and_rejects_stub_model_activation(monkeypatch):
    from soc_agent.application.analysis import build_soc_analysis_service
    from soc_agent.llm import SocLLMSettings

    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "apply")
    with pytest.raises(ValueError, match="requires SOC_ANALYZER_MODE=llm"):
        build_soc_analysis_service(settings=SocLLMSettings())


@pytest.mark.parametrize("flag,expected", [(None, False), ("false", False), ("true", True), ("invalid", None)])
def test_application_wires_reference_validation_from_operator_environment(monkeypatch, flag, expected):
    from types import SimpleNamespace

    import soc_agent.application.analysis as application
    from soc_agent.llm import SocLLMSettings

    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "shadow")
    name = "SOC_NORMALIZATION_REFERENCE_VALIDATION_ENABLED"
    if flag is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, flag)
    monkeypatch.setattr(application, "_build_post_analysis_observers", lambda *a, **kw: ())
    monkeypatch.setattr(application, "_build_analysis_request_enricher", lambda *a, **kw: None)
    monkeypatch.setattr(application, "get_app_config", lambda: SimpleNamespace(model_dump=lambda **kw: {}))
    monkeypatch.setattr(application, "build_configured_chat_client", lambda **kw: (Client(), "test"))
    monkeypatch.setattr(application, "build_configured_analysis_nodes", lambda **kw: (None, None))
    settings = SocLLMSettings(mode="llm")
    if expected is None:
        with pytest.raises(ValueError, match=name):
            application.build_soc_analysis_service(settings=settings)
    else:
        service = application.build_soc_analysis_service(settings=settings)
        assert service._runtime._normalization_reviewer.reference_validation_enabled is expected


def test_recovery_reuses_saved_review_only_with_identical_input_and_configuration():
    client = Client([fact()])
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")
    original = payload(alert())
    first = analyze_alert(original, normalization_reviewer=reviewer)
    second = analyze_alert(original, normalization_reviewer=reviewer, normalization_reuse=first)
    assert len(client.calls) == 1
    assert second.normalization_assistance.metadata["reused_from_run_id"] == first.run_id
    assert second.normalized_alert.entities.file == first.normalized_alert.entities.file
    changed = JsonLLMNormalizationReviewer(client=client, model_name="test", configuration_hash="changed")
    analyze_alert(original, normalization_reviewer=changed, normalization_reuse=first)
    assert len(client.calls) == 2


def test_service_rerun_reuses_facts_but_executes_current_analysis(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
    from soc_agent.core.service import DeterministicAnalysisRuntime, SocAnalysisService
    from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
    from soc_agent.demo.normalization_review import build_normalization_review_view

    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine))
    client = Client([fact()])
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")

    def service(refresh=False):
        return SocAnalysisService(runtime=DeterministicAnalysisRuntime(normalization_reviewer=reviewer, execution_options=SocAnalysisExecutionOptions(normalization_review_mode="apply", refresh_normalization=refresh)), repository=repository)

    original = payload(alert())
    first = service().analyze(original)
    second = service().analyze(original)
    assert len(client.calls) == 1
    assert first.run_id != second.run_id
    assert second.analysis is not None
    assert second.normalization_assistance.metadata["reused_from_run_id"] == first.run_id
    assert second.normalization_assistance.metadata["provider_call_count"] == 0
    assert second.normalization_assistance.metadata["usage"] == {}
    assert second.normalization_assistance.metadata["reference_validation_enabled"] is False
    assert "复用" in build_normalization_review_view(second).effect_label
    refreshed = service(True).analyze(original)
    assert len(client.calls) == 2
    assert refreshed.normalization_assistance.metadata["fact_snapshot_comparison"]["changed"] is False
    client.facts.append(fact("entities.host.host_name", "example", 'host="example"'))
    new = service(True).analyze(original)
    assert len(client.calls) == 3
    assert new.normalization_assistance.metadata["fact_snapshot_comparison"]["changed"] is True
    assert service().analyze(original).normalization_assistance.metadata["reused_from_run_id"] == new.run_id
    changed = payload(alert('host="other" file="D:\\tools\\yak.exe"'))
    service().analyze(changed)
    assert len(client.calls) == 4


def test_cache_never_reuses_another_input_or_failed_review():
    client = Client([fact()])
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")
    original = payload(alert())
    first = analyze_alert(original, normalization_reviewer=reviewer)
    first.input_hash = "a-different-complete-input"
    analyze_alert(original, normalization_reviewer=reviewer, normalization_reuse=first)
    assert len(client.calls) == 2
    first.input_hash = analyze_alert(original).input_hash
    first.normalization_assistance.status = "failed"
    analyze_alert(original, normalization_reviewer=reviewer, normalization_reuse=first)
    assert len(client.calls) == 3


def test_cached_review_does_not_rebill_historical_latency_or_usage():
    from soc_agent.core.normalization_snapshot import reused_report

    run = analyze_alert(payload(alert()), normalization_reviewer=JsonLLMNormalizationReviewer(client=Client(), model_name="test"))
    timings = {"admission_wait_duration_ms": 2, "client_total_duration_ms": 10500, "provider_duration_ms": 10498}
    run.normalization_assistance.metadata.update(timings)
    cached = reused_report(run)
    assert not (timings.keys() & cached.metadata.keys())
    assert cached.metadata["provider_call_count"] == 0
    assert cached.metadata["usage"] == {}
    assert all(run.normalization_assistance.metadata[key] == value for key, value in timings.items())


@pytest.mark.parametrize("variation", ["tenant", "shadow", "model", "configuration", "adapter"])
def test_snapshot_scope_and_version_changes_invalidate_reuse(variation):
    client = Client([fact()])
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")
    original = payload(alert())
    first = analyze_alert(original, normalization_reviewer=reviewer)
    if variation == "tenant":
        original["tenant_id"] = "other"
    elif variation == "adapter":
        original["entities"]["host"]["host_name"] = "adapter-correction"
    else:
        reviewer = JsonLLMNormalizationReviewer(
            client=client, model_name="other" if variation == "model" else "test", mode="shadow" if variation == "shadow" else "apply", configuration_hash="changed" if variation == "configuration" else "injected-client"
        )
    analyze_alert(original, normalization_reviewer=reviewer, normalization_reuse=first)
    assert len(client.calls) == 2


def test_explicit_replay_is_a_new_measurement_not_a_cache_hit():
    client = Client()
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="test")
    for _ in range(2):
        analyze_alert(payload(alert()), normalization_reviewer=reviewer)
    assert len(client.calls) == 2


def test_existing_hash_is_not_moved_to_another_file():
    source = alert()
    source.entities.file.md5 = "0" * 32
    reviewer = JsonLLMNormalizationReviewer(client=Client([fact()]), model_name="test")
    result = reviewer.review(source, reviewer.prepare(source))
    assert not result.changes
    assert "哈希" in result.issues[0]


def test_review_usage_is_counted_by_existing_effectiveness_projection():
    from soc_agent.db.repositories import _run_effectiveness_projection

    run = analyze_alert(payload(alert()), normalization_reviewer=JsonLLMNormalizationReviewer(client=Client(), model_name="test"))
    metrics = _run_effectiveness_projection(run)
    assert metrics["provider_call_count"] == 1
    assert metrics["total_tokens"] == run.normalization_assistance.metadata["usage"]["total_tokens"]


def test_provider_length_is_not_a_successful_review():
    class TruncatedClient:
        def complete(self, messages, *, model_name):
            return LLMChatResponse(content='{"facts": []}', metadata={"finish_reason": "length"}, usage={"prompt_tokens": 12, "completion_tokens": 8192, "total_tokens": 8204})

    reviewer = JsonLLMNormalizationReviewer(client=TruncatedClient(), model_name="test")
    result = reviewer.review(alert(), reviewer.prepare(alert()))
    assert result.status == "failed"
    assert result.metadata["finish_reason"] == "length"
    assert result.metadata["error_code"] == "output_length_limit"
    assert result.metadata["failure_kind"] == "analyzer_output_invalid"
    assert result.metadata["usage"]["total_tokens"] == 8204


def test_bad_optional_section_does_not_discard_a_valid_object():
    content = {"objects": [{"id": "p1", "kind": "process", "attributes": {"process_name": "System"}, "source_quote": "System"}], "events": "not a list", "unresolved": None}
    reviewer = JsonLLMNormalizationReviewer(client=Client(content=json.dumps(content)), model_name="test")
    source = alert('process="System"')
    result = reviewer.review(source, reviewer.prepare(source))
    assert result.status == "partial"
    assert len(result.observation_changes) == 1
    assert "invalid_events_list" in result.metadata["section_errors"]
    assert result.metadata["provider_call_count"] == 1


def test_additional_envelope_metadata_does_not_discard_facts():
    reviewer = JsonLLMNormalizationReviewer(client=Client(content=json.dumps({"facts": [fact()], "summary": "extra private commentary"})), model_name="test")
    source = alert()
    result = reviewer.review(source, reviewer.prepare(source))
    assert result.status == "applied"
    assert len(result.changes) == 1
    assert result.metadata["ignored_output_fields"] == ["summary"]
    assert "extra private commentary" not in result.model_dump_json()


@pytest.mark.parametrize("content,code", [("not JSON private text", "invalid_json"), ("[]", "invalid_output_envelope"), ("{}", "invalid_output_envelope")])
def test_failed_output_has_specific_diagnostics_without_raw_text(content, code):
    reviewer = JsonLLMNormalizationReviewer(client=Client(content=content), model_name="test")
    source = alert()
    result = reviewer.review(source, reviewer.prepare(source))
    assert result.status == "failed"
    assert result.metadata["error_code"] == code
    assert result.metadata["failure_stage"] == "parse_output"
    assert result.metadata["response_sha256"]
    assert "private text" not in result.model_dump_json()


@pytest.mark.parametrize("kind", ["httpx", "openai"])
def test_sdk_timeouts_are_reported_as_timeouts(kind):
    import openai

    req = httpx.Request("POST", "https://example.invalid")
    error = openai.APITimeoutError(request=req) if kind == "openai" else httpx.ReadTimeout("private", request=req)
    reviewer = JsonLLMNormalizationReviewer(client=Client(error=error), model_name="test")
    source = alert()
    result = reviewer.review(source, reviewer.prepare(source))
    assert result.metadata["failure_kind"] == "analyzer_timeout"
    assert result.metadata["failure_stage"] == "provider_call"
    assert result.metadata["error_code"] == "provider_timeout"


def test_review_draft_does_not_copy_unbounded_unreviewed_entities():
    source = alert()
    source.entities.threat.iocs = ["unreviewed-context"] * 10000
    source.entities.process.command_line = "x" * 60000
    source.extensions["source_field_semantics"] = [{"explanation": "y" * 1500}] * 40
    reviewer = JsonLLMNormalizationReviewer(client=Client(), model_name="test")
    request = reviewer.prepare(source)
    assert not request.adapter_entities.threat.iocs
    assert len(request.adapter_entities.process.command_line) == 4096
    assert request.omitted_draft_paths == ["entities.process.command_line"]
    assert request.omitted_semantic_count > 0
    assert len(request.model_dump_json()) < 20000
    assert reviewer.review(source, request).status == "partial"
    assert len(source.entities.process.command_line) == 60000
