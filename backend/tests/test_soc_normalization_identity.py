"""Detection identifiers retain their namespace and source, not model preference."""

import json

from soc_agent.contracts import AlertInput
from soc_agent.llm.analyzer import LLMChatResponse
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer, apply_normalization_changes


class Client:
    def __init__(self, event):
        self.event = event

    def complete(self, messages, *, model_name):
        return LLMChatResponse(content=json.dumps({"events": [self.event]}), model_name=model_name)


def identifier(kind, field, value):
    return {"kind": kind, "source_field": field, "value": value}


def reviewed(identifiers=(), legacy=None, declared=()):
    alert = AlertInput(
        alert_id="typed-identity",
        entities={"network": {"protocol": "udp", "dst_port": 1194}},
        raw={"message": "rule_id=0x5dc9 sig_id=3518 OpenVPN"},
        extensions={
            "evidence_input_policy": {"name": "raw_message_first", "selected_input_path": "message", "selected_layer": "raw_message", "trust_level": "high"},
            "source_detection_identifiers": {"message": list(declared)},
        },
    )
    event = {"kind": "network_access", "name": "OpenVPN", "subject_refs": ["O0"], "source_quote": "OpenVPN", "identifiers": list(identifiers), "detector_id": legacy}
    reviewer = JsonLLMNormalizationReviewer(client=Client(event), model_name="test")
    request = reviewer.prepare(alert)
    report = reviewer.review(alert, request)
    return apply_normalization_changes(alert, request, report), report


def test_declared_source_identifiers_do_not_depend_on_llm_choice():
    declared = [identifier("rule", "rule_id", "0x5dc9"), identifier("signature", "_origin.sig_id", "3518")]
    a, ar = reviewed(legacy="0x5dc9", declared=declared)
    b, br = reviewed(legacy="3518", declared=declared)
    assert a.entities.detections and b.entities.detections, (ar.issues, br.issues)
    assert a.entities.detections[0].detector_id == b.entities.detections[0].detector_id == "0x5dc9"
    assert a.entities.detections[0].identifiers == b.entities.detections[0].identifiers
    assert len(a.entities.detections[0].identifiers) == 2
    assert a.entities.detections[0].identity_basis == "adapter_declared"


def test_typed_identifier_order_is_irrelevant_and_equal_numbers_do_not_collide():
    rule = identifier("rule", "rule_id", "42")
    signature = identifier("signature", "sig_id", "42")
    a, _ = reviewed([rule, signature])
    b, _ = reviewed([signature, rule, rule])
    c, _ = reviewed([signature])
    assert a.entities.detections[0].detector_id == b.entities.detections[0].detector_id == "42"
    assert c.entities.detections[0].detector_id == "signature:42"
    assert a.entities.detections[0].identifiers == b.entities.detections[0].identifiers


def test_ambiguous_same_kind_ids_do_not_arbitrarily_select_one():
    a, report = reviewed([identifier("rule", "rules[0].id", "42"), identifier("rule", "rules[1].id", "43")])
    assert a.entities.detections[0].detector_id is None
    assert len(a.entities.detections[0].identifiers) == 2
    assert a.entities.detections[0].identity_basis == "ambiguous"
    assert any("标识" in issue for issue in report.issues)
    from types import SimpleNamespace

    from soc_agent.integrations.pingan.memory.semantic_features import semantic_behavior_components, semantic_projection_gaps

    request = SimpleNamespace(canonical_entities=a.entities, source=a.source)
    assert semantic_projection_gaps(request)
    assert not semantic_behavior_components(request, stable=True)[1]


def test_malformed_optional_identifier_does_not_erase_the_detection():
    updated, report = reviewed([identifier("unsupported-kind", "vendor_id", "x"), identifier("signature", "sig_id", 3518)])
    assert updated.entities.detections[0].detector_id == "signature:3518"
    assert len(updated.entities.detections[0].identifiers) == 1
    assert report.issues


def test_model_cannot_reclassify_or_override_adapter_identity():
    updated, _ = reviewed(
        [identifier("signature", "rule_id", "different"), identifier("rule", "invented_rule_field", "123")],
        declared=[identifier("rule", "rule_id", "0x5dc9")],
    )
    assert updated.entities.detections[0].detector_id == "0x5dc9"


def test_declared_file_detector_is_not_a_file_hash():
    from soc_agent.contracts.normalization import DetectionIdentifier
    from soc_agent.normalizers.detection_identity import resolve_detection_identity

    result = resolve_detection_identity([], [DetectionIdentifier(kind="malware", source_field="virus_id", value="a" * 32)], None)
    assert result["detector_id"] == "malware:" + "a" * 32
    assert "md5" not in result


def test_pingan_aliases_are_scoped_to_their_own_source():
    from soc_agent.contracts import ParsedRawMessageEvidence
    from soc_agent.normalizers.pingan_identity import source_detection_identifiers

    messages = [
        ParsedRawMessageEvidence(source_path="message1", parser_name="test", parser_version="1", message_hash="first", original_length=50, fields={"rule_id": "0x5dc9", "_origin": {"sig_id": 3518}}),
        ParsedRawMessageEvidence(source_path="message2", parser_name="test", parser_version="1", message_hash="second", original_length=80, fields={"rule_id": "0x4ef7", "_origin": {"sig_id": 1742}, "md5": "a" * 32}),
    ]
    result = source_detection_identifiers(messages)
    assert result["message1"] == [identifier("rule", "rule_id", "0x5dc9"), identifier("signature", "_origin.sig_id", "3518")]
    assert result["message2"][0]["value"] == "0x4ef7"
    assert all(item["value"] != "a" * 32 for item in result["message2"])
