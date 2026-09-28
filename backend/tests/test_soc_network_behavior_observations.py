"""Synthetic admission checks for reusable, source-bound network behavior facts."""

import json

import pytest

from soc_agent.contracts import AlertInput, HttpObservationRef
from soc_agent.contracts.normalization import SupplementaryFactRef
from soc_agent.llm.analyzer import LLMChatResponse
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer, apply_normalization_changes


class Client:
    def __init__(self, output):
        self.output = output

    def complete(self, messages, *, model_name):
        return LLMChatResponse(content=json.dumps(self.output), model_name=model_name)


def source(text):
    return AlertInput(
        alert_id="synthetic-network-behavior",
        source={"integration_name": "synthetic", "source_type": "nids"},
        detection={"detection_key": "synthetic:rule:1"},
        raw={"message": text},
        extensions={"evidence_input_policy": {"name": "raw_message_first", "selected_input_path": "message", "selected_layer": "raw_message", "trust_level": "high"}},
    )


def output(text, *, meaning="HTTP 响应返回目录列表", descriptor=None):
    return {
        "objects": [{"id": "h1", "kind": "http", "attributes": {"status_code": 200}, "source_quote": text}],
        "additional_facts": [
            {
                "name": "http_response_title",
                "value": "Directory listing for /apps/",
                "meaning": meaning,
                "subject_ref": "h1",
                "source_quote": text,
                "network_behavior": descriptor or {"kind": "http_response_directory_listing"},
            }
        ],
    }


def review(alert, payload, *, mode="apply", strict=False):
    reviewer = JsonLLMNormalizationReviewer(client=Client(payload), model_name="synthetic", mode=mode, reference_validation_enabled=strict)
    request = reviewer.prepare(alert)
    report = reviewer.review(alert, request)
    return apply_normalization_changes(alert, request, report), report, request


TEXT = "HTTP/1.1 200 OK\nServer: SimpleHTTP/0.6 Python/2.7.5\n\n<title>Directory listing for /apps/</title>"


@pytest.mark.parametrize("meaning", ["HTTP 响应返回目录列表", "页面列出了当前目录中的文件", "The response contains a directory listing"])
def test_paraphrases_preserve_stable_behavior_code_with_strict_global_check_off(meaning):
    updated, report, _ = review(source(TEXT), output(TEXT, meaning=meaning))
    assert report.status == "applied"
    fact = updated.entities.supplementary_facts[0]
    assert fact.network_behavior.kind == "http_response_directory_listing"
    assert fact.network_behavior_verification == "source_bound_v1"
    assert fact.subject_ref == "entities.http.observations[0]"
    change = next(c for c in report.observation_changes if "supplementary_facts" in c.target)
    assert change.reference_validation_status == "verified"
    assert change.source_start == 0


@pytest.mark.parametrize("problem", ["invented_quote", "value_not_quoted", "invented_object", "missing_subject", "wrong_kind", "unknown_subject", "spoofed_verification"])
def test_unverified_network_metadata_never_becomes_a_reusable_behavior(problem):
    payload = output(TEXT)
    fact = payload["additional_facts"][0]
    if problem == "invented_quote":
        fact["source_quote"] = "HTTP/1.1 200 OK\nDirectory listing for /invented/"
    elif problem == "value_not_quoted":
        fact["value"] = "unobserved directory title"
    elif problem == "invented_object":
        payload["objects"][0]["source_quote"] = "fabricated response 200"
    elif problem == "missing_subject":
        fact.pop("subject_ref")
    elif problem == "wrong_kind":
        payload["objects"][0].update(kind="network", attributes={"protocol": "HTTP"})
    elif problem == "unknown_subject":
        fact["subject_ref"] = "does-not-exist"
    else:
        fact.pop("network_behavior")
        fact["network_behavior_verification"] = "source_bound_v1"
    updated, report, _ = review(source(TEXT), payload)
    assert report.status == "partial"
    assert len(updated.entities.supplementary_facts) == 1
    assert updated.entities.supplementary_facts[0].network_behavior is None
    assert updated.entities.supplementary_facts[0].network_behavior_verification is None


@pytest.mark.parametrize(
    "descriptor",
    [
        {"kind": "some_new_behavior"},
        {"kind": "http_response_directory_listing", "ip": "10.0.0.1"},
        {"kind": "http_response_directory_listing", "server_product": "simplehttp"},
        {"kind": "http_response_server_banner", "server_product": "unrecognized-product"},
    ],
)
def test_unsupported_types_and_arbitrary_values_remain_ordinary_facts(descriptor):
    updated, report, _ = review(source(TEXT), output(TEXT, descriptor=descriptor))
    assert report.status == "partial"
    fact = updated.entities.supplementary_facts[0]
    assert fact.value == "Directory listing for /apps/"
    assert fact.network_behavior is None


@pytest.mark.parametrize("product,accepted", [("simplehttp", True), ("nginx", False)])
def test_banner_product_must_be_observed_not_inferred(product, accepted):
    payload = output(TEXT, descriptor={"kind": "http_response_server_banner", "server_product": product})
    payload["additional_facts"][0].update(name="server", value="SimpleHTTP/0.6 Python/2.7.5", meaning="响应头声明的软件")
    updated, _, _ = review(source(TEXT), payload)
    fact = updated.entities.supplementary_facts[0]
    assert bool(fact.network_behavior) == accepted
    if accepted:
        assert fact.network_behavior.server_product == "simplehttp"


@pytest.mark.parametrize("escaped", [False, True])
def test_structured_server_header_stays_bound_to_its_own_value(escaped):
    header = '{"name":"Server","value":"SimpleHTTP/0.6 Python/2.7.5"}'
    quote = header.replace('"', '\\"') if escaped else header
    text = "status=200 response_headers=[" + quote + "]"
    payload = output(text, descriptor={"kind": "http_response_server_banner", "server_product": "simplehttp"})
    payload["additional_facts"][0].update(value="SimpleHTTP/0.6 Python/2.7.5", source_quote=quote)
    alert = declared_source({"headers": [json.loads(header)], "status": 200}, {"headers": "observed_HTTP_response_headers"})
    alert.raw["message"] = text
    updated, report, _ = review(alert, payload)
    assert report.status == "applied"
    assert updated.entities.supplementary_facts[0].network_behavior.server_product == "simplehttp"


@pytest.mark.parametrize("headers", ["Server: nginx\nX-Other: SimpleHTTP/0.6", '{"name":"Server","value":"nginx"},{"name":"X-Other","value":"SimpleHTTP/0.6"}'])
def test_banner_does_not_borrow_value_from_another_header(headers):
    text = "status=200 " + headers
    payload = output(text, descriptor={"kind": "http_response_server_banner", "server_product": "simplehttp"})
    payload["additional_facts"][0].update(value="SimpleHTTP/0.6", source_quote=headers)
    alert = declared_source({"headers": headers, "status": 200}, {"headers": "observed_HTTP_response_headers"})
    alert.raw["message"] = text
    updated, report, _ = review(alert, payload)
    assert report.status == "partial"
    assert updated.entities.supplementary_facts[0].network_behavior is None


def test_duplicate_excerpt_with_one_http_observation_uses_grounded_representative_span():
    text = TEXT + "\ndecoded_copy=" + TEXT
    payload = output(text)
    payload["additional_facts"][0]["source_quote"] = "<title>Directory listing for /apps/</title>"
    updated, report, _ = review(source(text), payload)
    assert report.status == "applied"
    assert updated.entities.supplementary_facts[0].network_behavior.kind == "http_response_directory_listing"
    change = next(c for c in report.observation_changes if "supplementary_facts" in c.target)
    assert text[change.source_start : change.source_end] == change.source_quote


def test_cross_source_subject_does_not_discard_ordinary_fact_or_gain_behavior():
    alert = source(TEXT)
    alert.raw["other"] = "HTTP/1.1 404 Not Found"
    alert.extensions["evidence_input_policy"]["supplementary_input_paths"] = ["other"]
    payload = output(TEXT)
    payload["objects"][0].update(source_id="L1", source_quote=alert.raw["other"], attributes={"status_code": 404})
    updated, report, _ = review(alert, payload)
    assert report.status == "partial"
    assert len(updated.entities.supplementary_facts) == 1
    assert updated.entities.supplementary_facts[0].subject_ref is None
    assert updated.entities.supplementary_facts[0].network_behavior is None


def test_aggregate_http_cannot_bind_several_distinct_observations():
    alert = source(TEXT)
    alert.entities.http.status_code = 200
    alert.entities.http.observations = [
        HttpObservationRef(observation_id="one", evidence_path="message#http.one", status_code=200),
        HttpObservationRef(observation_id="two", evidence_path="message#http.two", status_code=200),
    ]
    payload = output(TEXT)
    payload["objects"] = []
    payload["additional_facts"][0]["subject_ref"] = "O0"
    updated, report, _ = review(alert, payload)
    assert report.status == "partial"
    assert updated.entities.supplementary_facts[0].network_behavior is None


def test_explicit_ref_does_not_prove_transaction_ownership_inside_shared_source():
    alert = source(TEXT)
    alert.entities.http.observations = [
        HttpObservationRef(observation_id="one", evidence_path="message#http.one", status_code=200),
        HttpObservationRef(observation_id="two", evidence_path="message#http.two", status_code=200),
    ]
    payload = output(TEXT)
    payload["objects"] = []
    payload["additional_facts"][0]["subject_ref"] = "O0"
    updated, report, _ = review(alert, payload)
    assert report.status == "partial"
    assert updated.entities.supplementary_facts[0].network_behavior is None


def test_aggregate_and_only_observation_use_one_canonical_subject():
    alert = source(TEXT)
    alert.entities.http.status_code = 200
    alert.entities.http.observations = [HttpObservationRef(observation_id="only", evidence_path="message#http", status_code=200)]
    payload = output(TEXT)
    payload["objects"] = []
    payload["additional_facts"][0]["subject_ref"] = "O0"
    other = dict(payload["additional_facts"][0], name="second_reference", subject_ref="O1")
    payload["additional_facts"].append(other)
    updated, report, _ = review(alert, payload)
    assert report.status == "applied"
    assert {fact.subject_ref for fact in updated.entities.supplementary_facts} == {"entities.http.observations[0]"}


def test_existing_alias_cannot_hide_unverified_object_rewrite():
    alert = source(TEXT)
    alert.entities.http.status_code = 404
    payload = output(TEXT)
    payload["objects"][0].update(existing_ref="O0", source_quote="invented status 200")
    payload["additional_facts"][0]["subject_ref"] = "O0"
    updated, report, _ = review(alert, payload)
    assert report.status == "partial"
    assert updated.entities.http.status_code == 200
    assert updated.entities.supplementary_facts[0].network_behavior is None


def test_aggregate_only_unverified_attribute_does_not_taint_unchanged_adapter_observation():
    alert = source(TEXT)
    alert.entities.http.status_code = 200
    alert.entities.http.host = "adapter-known-host"
    alert.entities.http.observations = [HttpObservationRef(observation_id="only", evidence_path="message#http", status_code=200)]
    payload = output(TEXT)
    # The ordinary review may retain this unrelated unchecked protocol proposal.
    # Typed behavior binds the unchanged adapter observation, not this aggregate.
    payload["objects"][0].update(existing_ref="O0", attributes={"status_code": 200, "protocol": "HTTP/1.0"}, source_quote="HTTP/1.1 200 OK")
    payload["additional_facts"][0]["subject_ref"] = "O0"
    updated, report, _ = review(alert, payload)
    assert report.status == "applied"
    fact = updated.entities.supplementary_facts[0]
    assert fact.network_behavior.kind == "http_response_directory_listing"
    assert fact.subject_ref == "entities.http.observations[0]"
    assert updated.entities.http.observations[0].protocol is None


def test_aggregate_alias_cannot_hide_unverified_canonical_observation_write():
    alert = source(TEXT)
    alert.entities.http.status_code = 200
    alert.entities.http.observations = [HttpObservationRef(observation_id="only", evidence_path="message#http", status_code=404)]
    payload = output(TEXT)
    payload["objects"][0].update(existing_ref="O1", source_quote="invented status 200")
    payload["additional_facts"][0]["subject_ref"] = "O0"
    updated, report, _ = review(alert, payload)
    assert report.status == "partial"
    assert updated.entities.http.observations[0].status_code == 200
    assert updated.entities.supplementary_facts[0].network_behavior is None


def test_conflicting_aggregate_still_cannot_alias_adapter_observation():
    alert = source(TEXT)
    alert.entities.http.status_code = 200
    alert.entities.http.observations = [HttpObservationRef(observation_id="only", evidence_path="message#http", status_code=200)]
    payload = output(TEXT)
    payload["objects"][0].update(existing_ref="O0", attributes={"status_code": 500}, source_quote="invented status 500")
    payload["additional_facts"][0]["subject_ref"] = "O0"
    updated, report, _ = review(alert, payload)
    assert report.status == "partial"
    assert updated.entities.supplementary_facts[0].network_behavior is None


def test_rule_prose_without_observed_http_response_stays_ordinary_fact():
    text = 'rule="Detect HTTP Directory listing for /apps/"'
    payload = output(text)
    payload["objects"][0]["attributes"] = {"protocol": "HTTP"}
    updated, report, _ = review(source(text), payload)
    assert report.status == "partial"
    assert updated.entities.supplementary_facts[0].network_behavior is None


def declared_source(fields, roles):
    text = json.dumps(fields, ensure_ascii=False)
    alert = source(text)
    alert.extensions["parsed_raw_messages"] = [{"source_path": "message", "fields": fields}]
    alert.extensions["source_field_semantics"] = [
        {"field_path": "message#parsed." + path, "semantic_type": role, "meaning": "adapter-owned content position", "participates_in_entities": False, "participates_in_reasoning": True} for path, role in roles.items()
    ]
    return alert


def test_rule_prose_plus_real_status_cannot_substitute_for_response_body():
    alert = declared_source({"rule": "Detect HTTP Directory listing for /apps/", "status": 200, "response_body": "Access denied"}, {"response_body": "observed_HTTP_response_body"})
    payload = output(alert.raw["message"])
    payload["additional_facts"][0]["source_quote"] = '"rule": "Detect HTTP Directory listing for /apps/"'
    updated, report, _ = review(alert, payload)
    assert report.status == "partial"
    assert updated.entities.supplementary_facts[0].network_behavior is None


@pytest.mark.parametrize("role", ["vendor_detection_description", "observed_HTTP_request_headers"])
def test_description_or_referer_cannot_supply_observed_response_content(role):
    alert = declared_source({"status": 200, "text": "Directory listing for /apps/"}, {"text": role})
    updated, report, _ = review(alert, output(alert.raw["message"]))
    assert report.status == "partial"
    assert updated.entities.supplementary_facts[0].network_behavior is None


def test_adapter_declared_body_uses_generic_role_not_vendor_field_name():
    alert = declared_source({"status": 200, "arbitrary_vendor_field": "Directory listing for /apps/"}, {"arbitrary_vendor_field": "observed_HTTP_response_body"})
    updated, report, _ = review(alert, output(alert.raw["message"]))
    assert report.status == "applied"
    assert updated.entities.supplementary_facts[0].network_behavior.kind == "http_response_directory_listing"


def test_one_model_http_object_cannot_combine_separate_literal_responses():
    text = "HTTP/1.1 200 OK\nServer: SimpleHTTP/0.6\n\nAccess denied\nHTTP/1.1 200 OK\nServer: nginx\n\n<title>Directory listing for /apps/</title>"
    payload = output(text)
    payload["additional_facts"].append(
        {"name": "server", "value": "SimpleHTTP/0.6", "meaning": "Server 响应头", "subject_ref": "h1", "source_quote": "Server: SimpleHTTP/0.6", "network_behavior": {"kind": "http_response_server_banner", "server_product": "simplehttp"}}
    )
    updated, report, _ = review(source(text), payload)
    assert report.status == "partial"
    assert len(updated.entities.supplementary_facts) == 2
    assert all(fact.network_behavior is None for fact in updated.entities.supplementary_facts)


def test_structured_body_cannot_promote_contents_of_an_embedded_http_message():
    alert = declared_source({"status": 200, "body": "Access denied\nHTTP/1.1 200 OK\n\n<title>Directory listing for /apps/</title>"}, {"body": "observed_HTTP_response_body"})
    updated, report, _ = review(alert, output(alert.raw["message"]))
    assert report.status == "partial"
    assert updated.entities.supplementary_facts[0].network_behavior is None


@pytest.mark.parametrize(
    "kind,text,value,attributes",
    [
        ("http_response_command_output", "HTTP/1.1 200 OK\n\nuid=1000(user)", "uid=1000(user)", {"status_code": 200}),
        ("http_response_file_content", "HTTP/1.1 200 OK\n\nroot:x:0:0:root:/root:/bin/bash", "root:x:0:0:root:/root:/bin/bash", {"status_code": 200}),
        ("http_request_directory_traversal", "GET /../../private HTTP/1.1\n\n", "/../../private", {"method": "GET", "path": "/../../private"}),
        ("http_request_command_execution", "POST /execute HTTP/1.1\n\ncmd=id", "cmd=id", {"method": "POST", "path": "/execute"}),
        ("http_request_file_upload", "POST /upload HTTP/1.1\n\nContent-Disposition: form-data; name=upload; filename=test.txt\nfile contents", "filename=test.txt", {"method": "POST", "path": "/upload"}),
    ],
)
def test_fixed_types_apply_to_observed_request_or_response_contents(kind, text, value, attributes):
    payload = output(text, descriptor={"kind": kind})
    payload["objects"][0]["attributes"] = attributes
    payload["additional_facts"][0]["value"] = value
    updated, report, _ = review(source(text), payload)
    assert report.status == "applied"
    assert updated.entities.supplementary_facts[0].network_behavior.kind == kind


def test_nids_adapter_declares_observed_http_content_not_rule_or_referer():
    from soc_agent.contracts import AlertSourceType
    from soc_agent.normalizers.pingan_messages import parse_pingan_raw_message
    from soc_agent.normalizers.pingan_platform import _source_field_semantics

    fields = {
        "alert": {"signature": "Detect directory listing"},
        "http": {"status": 200, "url": "/download", "referer": "/old", "http_response_body_printable": "Directory listing for /apps/", "response_headers": [{"name": "Server", "value": "SimpleHTTP/0.6"}], "http_request_body": "action=list"},
    }
    parsed = parse_pingan_raw_message(json.dumps(fields), source_path="message")
    semantics = _source_field_semantics(AlertSourceType.NIDS, [parsed], fallback_fields={}, fallback_path="")
    roles = {item["field_path"]: item["semantic_type"] for item in semantics}
    assert roles["message#parsed.http.http_response_body_printable"] == "observed_HTTP_response_body"
    assert roles["message#parsed.http.response_headers"] == "observed_HTTP_response_headers"
    assert roles["message#parsed.http.http_request_body"] == "observed_HTTP_request_body"
    assert roles["message#parsed.http.url"] == "observed_HTTP_request_target"
    assert "message#parsed.alert.signature" not in roles
    assert "message#parsed.http.referer" not in roles


def test_nids_adapter_does_not_declare_missing_uri_placeholder_as_request_target():
    from soc_agent.contracts import AlertSourceType
    from soc_agent.normalizers.pingan_messages import parse_pingan_raw_message
    from soc_agent.normalizers.pingan_platform import _source_field_semantics

    parsed = parse_pingan_raw_message(json.dumps({"http": {"status": 200, "url": "/libhtp::request_uri_not_seen"}}), source_path="message")
    semantics = _source_field_semantics(AlertSourceType.NIDS, [parsed], fallback_fields={}, fallback_path="")
    assert all(item["semantic_type"] != "observed_HTTP_request_target" for item in semantics)


def test_prompt_http_example_is_admissible_and_records_one_subject():
    from soc_agent.prompts.normalization import build_normalization_prompt

    reviewer = JsonLLMNormalizationReviewer(client=Client({}), model_name="synthetic")
    prompt = build_normalization_prompt(reviewer.prepare(source(TEXT)))[0]["content"]
    examples = json.loads(prompt.split("<examples>\n", 1)[1].split("\n</examples>", 1)[0])
    example = next(item for item in examples if any(fact.get("network_behavior") for fact in item["output"]["additional_facts"]))
    updated, report, _ = review(source(example["source"]), example["output"])
    assert report.status == "applied"
    assert len(updated.entities.supplementary_facts) == 2
    assert {fact.subject_ref for fact in updated.entities.supplementary_facts} == {"entities.http.observations[0]"}
    assert "Referer" in prompt and "编号" in prompt and "规则说明" in prompt


def test_shadow_does_not_expose_new_matching_behavior():
    alert = source(TEXT)
    updated, report, _ = review(alert, output(TEXT), mode="shadow")
    assert updated == alert
    assert report.observation_changes


def test_legacy_fact_serialization_keeps_exact_original_fields():
    old = {"observation_id": "old", "evidence_path": "message", "event_scope_id": "message", "name": "count", "value": 2, "meaning": "count", "subject_ref": None, "clue_type": None}
    assert SupplementaryFactRef.model_validate(old).model_dump(mode="json") == old
