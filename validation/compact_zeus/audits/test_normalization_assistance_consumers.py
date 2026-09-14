"""Consumer checks are not model extraction or security-accuracy labels."""

import copy

import pytest

from validation.compact_zeus.audits.normalization_assistance_consumers import (
    compare_cases,
    literal_control_facts,
    review_case,
)
from validation.compact_zeus.audits.normalization_assistance_trial import select_source
from validation.compact_zeus.audits.quoted_kv_parser_audit import compare_payload


def payload(*, host="host-a", ip="10.1.2.3", file="sample.exe", virus="Family.A"):
    return {
        "tenant_id": "pingan",
        "alert": {
            "id": "synthetic-a",
            "ruleCode": "SYNTHETIC_AV",
            "ruleName": "Synthetic AV detector",
            "hitLog": [
                {
                    "topic": "ifds-xc-virus",
                    "zeusRawLogs": [
                        {
                            "message": (
                                f'hostname="{host}" client_ip="{ip}" '
                                'proc_path="C:\\Windows\\explorer.exe" '
                                f'file_path="D:\\Samples\\{file}" '
                                f'virus_name="{virus}" virus_type="Trojan" result="0"'
                            )
                        }
                    ],
                }
            ],
        },
    }


def test_literal_control_is_explicit_and_does_not_guess_results_or_hash_ownership():
    source = select_source(payload())
    facts = literal_control_facts(source)
    assert len(facts) == 4
    assert all(item["value"] in item["source_quote"] for item in facts)
    assert all(item["source_quote"] in source["text"] for item in facts)
    report = review_case(payload())
    assert report["extraction_origin"] == "literal_test_control_not_llm"
    assert report["new_model_calls"] == 0
    assert report["source_fields"]["result"] == ["0"]
    assert (
        report["canonical"]["entities"]["file"]["observations"][0]["relation"]
        == "observed_artifact"
    )
    assert (
        report["canonical"]["entities"]["file"]["observations"][0]["process_id"] is None
    )


def test_parser_audit_compares_legacy_empty_command_to_current_value():
    original = payload()
    original["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"] += (
        ' cmdline=""C:\\tools\\example.exe" --check"'
    )
    untouched = copy.deepcopy(original)
    report = compare_payload(original)
    assert report["before_v2"]["fields"]["cmdline"] == ""
    assert report["after"]["fields"]["cmdline"] == '"C:\\tools\\example.exe" --check'
    assert report["changed_fields"] == ["cmdline"]
    assert report["changed_fields_visible_to_model"] == {"cmdline": True}
    assert report["after"]["syntax_coverage"]["complete"]
    assert report["raw_preserved"] and original == untouched


@pytest.mark.parametrize(
    "message", ['hostname="a" hostname="b"', 'hostname="a" hostname="a"']
)
def test_literal_controls_refuse_duplicate_fields(message):
    with pytest.raises(ValueError, match="duplicate"):
        literal_control_facts({"text": message})


def test_process_label_is_not_invented_as_absolute_path():
    facts = literal_control_facts(
        {"text": 'proc_path="System" file_path="D:\\sample.exe"'}
    )
    assert [item["target"] for item in facts] == ["entities.file.file_path"]


def test_current_consumer_blind_spot_is_visible_without_claiming_wrong_verdict():
    left = review_case(payload(file="a.exe", virus="Family.A"))
    right = review_case(payload(file="b.exe", virus="Family.B"))
    pair = compare_cases(left, right)
    assert pair["same_nonempty_fingerprint"]
    assert pair["same_required_scope"]
    assert pair["detector_or_file_difference_unrepresented"]
    assert pair["different_source_fields"] == ["file_path", "virus_name"]
    assert not pair["memory_retrieval_executed"]


def test_host_ip_variation_does_not_split_behavior_and_is_not_a_collision():
    left = review_case(payload())
    right = review_case(payload(host="host-b", ip="10.2.3.4"))
    pair = compare_cases(left, right)
    assert pair["same_nonempty_fingerprint"]
    assert not pair["detector_or_file_difference_unrepresented"]


def test_case_review_does_not_mutate_payload_and_exposes_field_destinations():
    data = payload()
    original = copy.deepcopy(data)
    report = review_case(data)
    assert data == original
    file_row = next(
        row
        for row in report["field_destinations"]
        if row["source_field"] == "file_path"
    )
    assert file_row["canonical_targets"] == ["entities.file.file_path"]
    assert file_row["in_primary_evidence"]
    virus_row = next(
        row
        for row in report["field_destinations"]
        if row["source_field"] == "virus_name"
    )
    assert virus_row["in_primary_evidence"]
    assert virus_row["canonical_targets"] == []


def test_saved_proposals_remain_distinct_from_literal_controls():
    report = review_case(payload(), saved_facts=[])
    assert report["extraction_origin"] == "frozen_model_response"
    assert report["merge"]["accepted"] == []
    assert report["new_model_calls"] == 0


def test_consumer_report_uses_canonical_alert_id_not_one_raw_alias():
    data = payload()
    data["alert"]["alertId"] = data["alert"].pop("id")
    assert review_case(data)["alert_id"] == "synthetic-a"


def test_unhandled_multi_message_case_fails_instead_of_selecting_first():
    data = payload()
    data["alert"]["hitLog"][0]["zeusRawLogs"].append({"message": 'hostname="host-b"'})
    with pytest.raises(ValueError, match="exactly one"):
        review_case(data)
