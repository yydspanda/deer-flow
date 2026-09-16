"""Synthetic semantic-review controls; not real LLM quality evidence."""

import json

import pytest

from soc_agent.contracts import AlertInput, AnalysisRun
from soc_agent.core.runtime import analyze_alert
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.llm.analyzer import LLMChatResponse
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer, apply_normalization_changes
from soc_agent.pipeline.reference_catalog import finalize_analysis_reference_catalogs


class Client:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def complete(self, messages, *, model_name):
        self.calls += 1
        return LLMChatResponse(content=json.dumps(self.output), model_name=model_name)


def source(text):
    return AlertInput(
        alert_id="synthetic-observation",
        source={"integration_name": "pingan_legacy_alert_platform", "source_type": "edr"},
        detection={"detection_key": "synthetic:rule:1"},
        raw={"message": text},
        extensions={"evidence_input_policy": {"name": "raw_message_first", "selected_input_path": "message", "selected_layer": "raw_message", "trust_level": "high"}},
    )


def obj(key, kind, attributes, quote, **extra):
    return {"id": key, "kind": kind, "attributes": attributes, "source_quote": quote, **extra}


def review(alert, output, *, mode="apply", reference_validation_enabled=True):
    reviewer = JsonLLMNormalizationReviewer(client=Client(output), model_name="synthetic", mode=mode, reference_validation_enabled=reference_validation_enabled)
    request = reviewer.prepare(alert)
    report = reviewer.review(alert, request)
    return apply_normalization_changes(alert, request, report), report, request


@pytest.mark.parametrize("quote", ["explorer.exe", 'cmdline="explorer.exe"'])
def test_reference_validation_defaults_off_without_inventing_offsets(quote):
    alert = source('proc_path="C:\\windows\\explorer.exe" cmdline="explorer.exe "')
    output = {"objects": [obj("p1", "process", {"process_name": "explorer.exe", "command_line": "explorer.exe "}, quote)]}
    reviewer = JsonLLMNormalizationReviewer(client=Client(output), model_name="synthetic")
    request = reviewer.prepare(alert)
    report = reviewer.review(alert, request)
    updated = apply_normalization_changes(alert, request, report)
    assert request.reference_validation_enabled is False
    assert report.status == "applied"
    change = report.observation_changes[0]
    assert change.source_start is None and change.source_end is None
    assert change.reference_validation_status == "not_checked"
    assert report.metadata["reference_validation_enabled"] is False
    assert updated.entities.process.observations[0].nodes[0].command_line == "explorer.exe "
    assert all(p["selected_from"].endswith("#semantic-unverified") for p in updated.extensions["canonical_field_provenance"])


@pytest.mark.parametrize("quote,code", [("explorer.exe", "ambiguous_quote"), ('cmdline="explorer.exe"', "missing_quote")])
def test_strict_reference_validation_can_be_restored(quote, code):
    alert = source('proc_path="C:\\windows\\explorer.exe" cmdline="explorer.exe "')
    _, report, request = review(alert, {"objects": [obj("p1", "process", {"command_line": "explorer.exe "}, quote)]})
    assert request.reference_validation_enabled is True
    assert report.status == "partial"
    assert not report.observation_changes
    assert code in report.issues[0]


def test_disabling_reference_validation_preserves_types_and_source_membership():
    alert = source('ip="10.0.0.1"')
    _, report, _ = review(
        alert,
        {
            "objects": [
                obj("n1", "network", {"source_ip": "not-an-ip"}, "normalized quote"),
                obj("p1", "process", {"process_name": "tool.exe"}, "normalized quote", source_id="L99"),
            ]
        },
        reference_validation_enabled=False,
    )
    assert report.status == "partial"
    assert not report.observation_changes
    assert any("unknown_source" in issue for issue in report.issues)


def test_reference_validation_switch_is_frozen_in_request_and_hash():
    alert = source('process="tool.exe"')
    relaxed = JsonLLMNormalizationReviewer(client=Client({}), model_name="synthetic")
    strict = JsonLLMNormalizationReviewer(client=Client({}), model_name="synthetic", reference_validation_enabled=True)
    assert relaxed.prepare(alert).configuration_hash != strict.prepare(alert).configuration_hash


def test_strict_unique_quote_still_records_verified_offsets():
    text = 'cmd="tool.exe --check"'
    _, report, _ = review(source(text), {"objects": [obj("p1", "process", {"process_name": "tool.exe", "command_line": "tool.exe --check"}, text)]})
    assert report.status == "applied"
    assert report.observation_changes[0].source_start == 0
    assert report.observation_changes[0].source_end == len(text)
    assert report.observation_changes[0].reference_validation_status == "verified"


def test_unchecked_events_and_additional_facts_keep_same_source_ownership():
    text, output = file_output()
    output["events"][0]["source_quote"] = "short detector excerpt"
    output["events"].append({**output["events"][0], "source_id": "L1"})
    output["additional_facts"] = [{"name": "status", "value": "normalized status", "meaning": "上游报告。", "source_quote": "short excerpt", "subject_ref": "f1"}]
    alert = source(text)
    alert.raw["other"] = "another event"
    alert.extensions["evidence_input_policy"]["supplementary_input_paths"] = ["other"]
    updated, report, _ = review(alert, output, reference_validation_enabled=False)
    assert len(updated.entities.detections) == 1
    assert updated.entities.detections[0].subject_refs == ["entities.file.observations[0]"]
    assert len(updated.entities.supplementary_facts) == 1
    assert any("unknown_or_cross_event_subject" in issue for issue in report.issues)
    assert all(change.source_start is None for change in report.observation_changes)


def file_output(name="GoExec.a", category="HackTool", path=r"D:\tools\yak.exe"):
    text = f'file="{path}" md5="' + "a" * 32 + f'" name="{name}" type="{category}"'
    output = {
        "objects": [obj("f1", "file", {"file_path": path, "md5": "a" * 32}, text)],
        "events": [{"kind": "file_detection", "subject_refs": ["f1"], "name": name, "category": category, "source_quote": text}],
    }
    return text, output


def test_file_detection_is_bound_to_file_and_reaches_citable_model_input():
    text, output = file_output()
    alert = source(text)
    raw = {**alert.model_dump(mode="json"), "message": text}
    run = analyze_alert(raw, normalization_reviewer=JsonLLMNormalizationReviewer(client=Client(output), model_name="synthetic"))
    assert run.failure is None
    assert run.normalized_alert.raw == raw
    entities = run.normalized_alert.entities
    assert entities.file.observations[0].md5 == "a" * 32
    detection = entities.detections[0]
    assert detection.subject_refs == ["entities.file.observations[0]"]
    assert detection.name == "GoExec.a"
    assert detection.category == "HackTool"
    assert run.normalization_assistance.observation_changes
    assert all(c.model_input_status == "present" for c in run.normalization_assistance.observation_changes)
    request = finalize_analysis_reference_catalogs(run.llm_analysis_request)
    assert any(c.value == "GoExec.a" for c in request.evidence_catalog)
    assert any(c.value == "a" * 32 for c in request.evidence_catalog)
    assert any("detections" in p.canonical_path for p in request.fact_reconstruction.canonical_field_provenance)
    assert AnalysisRun.model_validate_json(run.model_dump_json()).normalization_assistance == run.normalization_assistance


def test_file_identity_does_not_move_existing_hash_to_another_file():
    text, output = file_output()
    alert = source(text)
    alert.entities.process.process_path = r"C:\bin\bash.exe"
    alert.entities.process.md5 = "b" * 32
    updated, report, _ = review(alert, output)
    assert report.status == "applied"
    assert updated.entities.process.md5 == "b" * 32
    assert updated.entities.file.observations[0].md5 == "a" * 32
    assert not alert.entities.file.observations


def test_empty_source_fact_survives_merge_without_empty_scalar_provenance():
    text, output = file_output()
    output["additional_facts"] = [{"name": "cmdline", "value": "", "meaning": "上游命令行为空。", "source_quote": text}]
    updated, report, _ = review(source(text), output)
    assert report.status == "applied"
    assert updated.entities.supplementary_facts[0].value == ""
    assert updated.entities.file.observations[0].file_name == "yak.exe"
    assert updated.entities.detections[0].name == "GoExec.a"
    assert all(p["selected_value"] != "" for p in updated.extensions["canonical_field_provenance"])


def test_parent_and_nested_observation_changes_have_independent_frozen_snapshots():
    from soc_agent.contracts import FileObservationRef

    alert = source('file="D:\\tools\\yak.exe" name="yak.exe"')
    alert.entities.file.file_path = r"D:\tools\yak.exe"
    alert.entities.file.observations = [FileObservationRef(observation_id="file-existing", evidence_path="message#parsed.file", relation="observed_artifact", file_path=r"D:\tools\yak.exe")]
    reviewer = JsonLLMNormalizationReviewer(client=Client({}), model_name="synthetic")
    request = reviewer.prepare(alert)
    primary = next(k for k, v in request.object_catalog.items() if v["path"] == "entities.file")
    nested = next(k for k, v in request.object_catalog.items() if v["path"] == "entities.file.observations[0]")
    reviewer.client.output = {"objects": [obj("f1", "file", {"file_name": "yak.exe"}, alert.raw["message"], existing_ref=primary), obj("f2", "file", {"file_name": "yak.exe"}, alert.raw["message"], existing_ref=nested)]}
    report = reviewer.review(alert, request)
    assert len(report.observation_changes) == 2
    assert report.observation_changes[0].after["observations"][0]["file_name"] is None
    frozen = report.model_dump_json()
    applied = apply_normalization_changes(alert, request, report)
    assert applied.entities.file.file_name == "yak.exe"
    assert applied.entities.file.observations[0].file_name == "yak.exe"
    assert report.model_dump_json() == frozen
    assert alert.entities.file.observations[0].file_name is None
    changed = alert.model_copy(deep=True)
    changed.entities.file.observations[0].md5 = "b" * 32
    with pytest.raises(ValueError, match="semantic_merge_snapshot_changed"):
        apply_normalization_changes(changed, request, report)


def test_same_scope_same_file_is_merged_not_duplicated():
    text, output = file_output()
    alert = source(text)
    from soc_agent.contracts import FileObservationRef

    alert.entities.file.observations = [FileObservationRef(observation_id="file-existing", evidence_path="message#parsed.file", relation="observed_artifact", file_path=r"D:\tools\yak.exe")]
    updated, _, _ = review(alert, output)
    assert len(updated.entities.file.observations) == 1
    assert updated.entities.file.observations[0].observation_id == "file-existing"
    assert updated.entities.file.observations[0].md5 == "a" * 32
    again, _, _ = review(updated, output)
    assert len(again.entities.file.observations) == 1
    assert len(again.entities.detections) == 1


def test_existing_process_can_supply_only_changed_command_without_copying_identity():
    from soc_agent.contracts import ProcessNodeRef, ProcessObservationRef

    alert = source('command="bash --login -i"')
    alert.entities.process.observations = [
        ProcessObservationRef(
            observation_id="existing-process",
            evidence_path="message#parsed",
            nodes=[ProcessNodeRef(process_name="bash", process_id=42, command_line="")],
        )
    ]
    reviewer = JsonLLMNormalizationReviewer(client=Client({}), model_name="synthetic")
    request = reviewer.prepare(alert)
    alias = next(k for k, v in request.object_catalog.items() if v["path"].endswith("nodes[0]"))
    updated, report, _ = review(
        alert,
        {
            "objects": [
                obj(
                    "p",
                    "process",
                    {"command_line": "bash --login -i"},
                    alert.raw["message"],
                    existing_ref=alias,
                )
            ]
        },
    )
    assert report.status == "applied"
    node = updated.entities.process.observations[0].nodes[0]
    assert node.process_id == 42
    assert node.command_line == "bash --login -i"
    changed_paths = [p["canonical_path"] for p in updated.extensions["canonical_field_provenance"]]
    assert changed_paths == ["entities.process.observations[0].nodes[0].command_line"]


def test_detection_reference_to_existing_primary_file_reaches_behavior_features():
    from types import SimpleNamespace

    from soc_agent.integrations.pingan.memory.semantic_features import semantic_behavior_components

    text, _ = file_output()
    alert = source(text)
    alert.entities.file.file_path = r"D:\tools\yak.exe"
    updated, report, _ = review(
        alert,
        {
            "events": [
                {
                    "kind": "file_detection",
                    "subject_refs": ["O0"],
                    "name": "GoExec.a",
                    "category": "HackTool",
                    "source_quote": text,
                }
            ]
        },
    )
    assert report.status == "applied"
    components, strong = semantic_behavior_components(SimpleNamespace(canonical_entities=updated.entities))
    assert "detected_file:yak.exe" in components
    assert strong


def test_empty_output_object_is_a_review_failure_not_success():
    alert = source("file=yak.exe")
    updated, report, _ = review(alert, {})
    assert report.status == "failed"
    assert updated == alert


def test_subject_reference_failure_is_local_not_whole_alert_failure():
    text, output = file_output()
    output["events"][0]["subject_refs"] = ["unknown"]
    updated, report, _ = review(source(text), output)
    assert report.status == "partial"
    assert len(updated.entities.file.observations) == 1
    assert not updated.entities.detections


def test_unbound_hash_and_statistic_remain_citable_facts():
    text = 'hash="' + "a" * 32 + '" count="2"'
    updated, report, _ = review(
        source(text),
        {
            "additional_facts": [
                {"name": "md5", "value": "a" * 32, "meaning": "日志报告的哈希，归属未明确。", "source_quote": text},
                {"name": "address_count", "value": 2, "meaning": "日志统计的地址数量。", "source_quote": text},
            ]
        },
    )
    assert report.status == "applied"
    assert len(updated.entities.supplementary_facts) == 2
    assert updated.entities.supplementary_facts[0].subject_ref is None
    assert updated.entities.file.md5 is None


@pytest.mark.parametrize(
    "kind,attrs,text",
    [
        ("network", {"destination_ip": "10.0.0.1", "dst_port": 8001}, 'target="10.0.0.1" port="8001"'),
        ("process", {"process_name": "System"}, 'process="System"'),
        ("container", {"container_name": "web", "namespace": "apps"}, 'container="web" namespace="apps"'),
        ("host", {"host_name": "pc-a"}, 'host="pc-a"'),
        ("user", {"username": "user-a"}, 'user="user-a"'),
    ],
)
def test_typed_object_families_are_accepted_without_vendor_aliases(kind, attrs, text):
    updated, report, _ = review(source(text), {"objects": [obj("o1", kind, attrs, text)]})
    assert report.status == "applied", report.issues
    assert report.observation_changes
    assert updated != source(text)


def test_bad_attribute_does_not_discard_valid_sibling_object():
    text, output = file_output()
    output["objects"].append(obj("bad", "http", {"method": "Cookie=a=b"}, 'method="Cookie=a=b"'))
    updated, report, _ = review(source(text + ' method="Cookie=a=b"'), output)
    assert report.status == "partial"
    assert len(updated.entities.file.observations) == 1
    assert not updated.entities.http.observations


def test_shadow_observations_do_not_mutate_input():
    text, output = file_output()
    alert = source(text)
    updated, report, _ = review(alert, output, mode="shadow")
    assert updated == alert
    assert report.observation_changes
    assert all(c.canonical_status == "shadow" for c in report.observation_changes)


def test_semantic_profile_is_versioned_and_file_detection_not_explorer_defines_pattern():
    from soc_agent.pipeline.analysis_context import build_llm_analysis_request
    from soc_agent.pipeline.extractor import extract_entities
    from soc_agent.pipeline.fact_reconstructor import reconstruct_facts

    profile = PingAnSocMemoryProfile(semantic_features=True)
    assert profile.identity != PingAnSocMemoryProfile().identity
    results = []
    for name, category, path in [("GoExec.a", "HackTool", r"D:\user-a\yak.exe"), ("GoExec.a", "HackTool", r"D:\user-b\yak.exe"), ("ShellLoader.auj", "Trojan", r"C:\tools\wirelesscm.exe")]:
        text, output = file_output(name, category, path)
        alert, report, _ = review(source(text), output)
        request = build_llm_analysis_request(alert, extract_entities(alert), reconstruct_facts(alert))
        facets = profile.project_query_facets(request)
        assert facets["behavior_strength"] == ["strong"]
        results.append(facets["behavior_fingerprint"])
    assert results[0] == results[1]
    assert results[0] != results[2]


def test_detection_features_keep_label_and_file_pairing():
    from types import SimpleNamespace

    from soc_agent.integrations.pingan.memory.semantic_features import semantic_behavior_components

    text = 'file="one.exe" type="Trojan" other="two.exe" kind="Worm"'
    features = []
    for targets in (["one", "two"], ["two", "one"]):
        output = {
            "objects": [obj("one", "file", {"file_name": "one.exe"}, text), obj("two", "file", {"file_name": "two.exe"}, text)],
            "events": [
                {"kind": "file_detection", "name": "Trojan", "subject_refs": [targets[0]], "source_quote": text},
                {"kind": "file_detection", "name": "Worm", "subject_refs": [targets[1]], "source_quote": text},
            ],
        }
        updated, _, _ = review(source(text), output)
        features.append(semantic_behavior_components(SimpleNamespace(canonical_entities=updated.entities)))
    assert features[0] != features[1]


@pytest.mark.parametrize("mode,version", [("off", "7"), ("shadow", "7"), ("apply", "8")])
def test_semantic_profile_rollout_does_not_change_off_or_shadow(monkeypatch, mode, version):
    from soc_agent.application.memory import build_soc_memory_profile_registry

    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", mode)
    assert build_soc_memory_profile_registry().get("pingan.soc").identity.profile_version == version


def test_invalid_object_id_does_not_erase_valid_file():
    text, output = file_output()
    output["objects"].append(obj([], "file", {"file_name": "yak.exe"}, text))
    updated, report, _ = review(source(text), output)
    assert report.status == "partial"
    assert len(updated.entities.file.observations) == 1


def test_catalog_omission_and_duplicate_source_are_auditable():
    alert = source('host="pc"')
    alert.raw["duplicate"] = alert.raw["message"]
    alert.extensions["evidence_input_policy"]["supplementary_input_paths"] = ["duplicate"]
    alert.entities.process.command_line = "x" * 3000
    _, report, request = review(alert, {"objects": []})
    assert request.deduplicated_source_count == 1
    assert request.omitted_object_count == 1
    assert request.omitted_object_paths == ["entities.process"]
    assert report.status == "partial"


def test_prompt_uses_task_language_and_sparse_typed_output():
    from soc_agent.prompts.normalization import build_normalization_prompt

    reviewer = JsonLLMNormalizationReviewer(client=Client({}), model_name="synthetic")
    prompt = build_normalization_prompt(reviewer.prepare(source('host="pc"')))[0]["content"]
    assert "Memory 决策" not in prompt
    assert "检测" in prompt and "subject_refs" in prompt and "additional_facts" in prompt


def test_prompt_business_clue_example_uses_existing_facts_and_separate_sources():
    from soc_agent.prompts.normalization import build_normalization_prompt

    reviewer = JsonLLMNormalizationReviewer(client=Client({}), model_name="synthetic")
    prompt = build_normalization_prompt(reviewer.prepare(source('host="pc"')))[0]["content"]
    examples = json.loads(prompt.split("<examples>\n", 1)[1].split("\n</examples>", 1)[0])
    example = next(e for e in examples if any(f["name"] == "payload_business_address" for f in e["output"]["additional_facts"]))
    alert = source(example["sources"][0]["text"])
    alert.raw["other"] = example["sources"][1]["text"]
    alert.extensions["evidence_input_policy"]["supplementary_input_paths"] = ["other"]
    updated, report, _ = review(alert, example["output"], reference_validation_enabled=False)

    assert report.status == "applied"
    clue = next(f for f in updated.entities.supplementary_facts if f.name == "payload_business_address")
    assert clue.value == "portal.example.invalid/apps/helpdesk"
    assert clue.event_scope_id == "message"
    assert updated.entities.network == alert.entities.network
    assert updated.entities.http == alert.entities.http
    assert updated.entities.detections[0].reported_result == "失陷"
    assert updated.entities.detections[0].event_scope_id == "other"
    assert [(c.source_id, c.source_path) for c in report.observation_changes if "detections" in c.target] == [("L1", "other")]
    assert "跨日志的信息分别输出" in prompt
    assert "不因出现业务域名就判定安全" in prompt


def test_existing_primary_command_can_be_corrected_with_exact_reference():
    text = 'cmd="tool.exe --check"'
    alert = source(text)
    alert.entities.process.process_name = "tool.exe"
    alert.entities.process.command_line = "tool.exe"
    updated, report, request = review(alert, {"objects": [obj("p1", "process", {"process_name": "tool.exe", "command_line": "tool.exe --check"}, text, existing_ref="O0")]})
    assert request.object_catalog["O0"]["path"] == "entities.process"
    assert updated.entities.process.command_line == "tool.exe --check"
    assert report.observation_changes[0].before["command_line"] == "tool.exe"


def test_invalid_optional_hash_does_not_discard_file_path():
    text = 'file="D:\\tools\\a.exe" md5="not-a-hash"'
    updated, report, _ = review(source(text), {"objects": [obj("f1", "file", {"file_path": r"D:\tools\a.exe", "md5": "not-a-hash"}, text)]})
    assert report.status == "partial"
    assert updated.entities.file.observations[0].file_path == r"D:\tools\a.exe"
    assert updated.entities.file.observations[0].md5 is None


def test_independent_source_objects_cannot_be_linked_into_one_detection():
    alert = source('file="D:\\a.exe"')
    alert.raw["other"] = 'name="Other.a"'
    alert.extensions["evidence_input_policy"]["supplementary_input_paths"] = ["other"]
    updated, report, _ = review(
        alert, {"objects": [obj("f1", "file", {"file_path": r"D:\a.exe"}, alert.raw["message"])], "events": [{"kind": "file_detection", "name": "Other.a", "subject_refs": ["f1"], "source_id": "L1", "source_quote": alert.raw["other"]}]}
    )
    assert report.status == "partial"
    assert not updated.entities.detections


def test_same_path_in_another_message_does_not_inherit_hash():
    text, output = file_output()
    alert = source(text)
    from soc_agent.contracts import FileObservationRef

    alert.entities.file.observations = [FileObservationRef(observation_id="other", evidence_path="other.message#parsed.file", relation="observed_artifact", file_path=r"D:\tools\yak.exe", md5="b" * 32)]
    updated, _, _ = review(alert, output)
    assert len(updated.entities.file.observations) == 2
    assert updated.entities.file.observations[0].md5 == "b" * 32
    assert updated.entities.file.observations[1].md5 == "a" * 32


def test_semantic_failure_never_creates_a_model_verdict():
    class BadClient:
        def complete(self, messages, *, model_name):
            return LLMChatResponse(content='{"objects": []}', metadata={"finish_reason": "length"})

    alert = source('file="x"')
    reviewer = JsonLLMNormalizationReviewer(client=BadClient(), model_name="synthetic")
    request = reviewer.prepare(alert)
    result = reviewer.review(alert, request)
    assert result.status == "failed"
    assert not result.observation_changes
    assert apply_normalization_changes(alert, request, result) == alert
