from __future__ import annotations

import json
from pathlib import Path

from soc_agent.contracts import AnalysisEvidenceCatalogItem
from soc_agent.core import SocAnalysisService
from soc_agent.llm import parse_analysis_result_output
from soc_agent.prompts import (
    ANALYSIS_PROMPT_VERSION,
    analysis_output_examples,
    build_analysis_prompt,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _analysis_request(sample_name: str):
    run = SocAnalysisService().analyze(_sample(sample_name))
    assert run.llm_analysis_request is not None
    return run.llm_analysis_request


def test_analysis_prompt_uses_bounded_llm_request_for_pingan_apt() -> None:
    prompt = build_analysis_prompt(_analysis_request("pingan_legacy_apt.json"))

    assert prompt.prompt_version == ANALYSIS_PROMPT_VERSION
    assert prompt.example_id == "network_roles"
    assert prompt.context["prompt_example_id"] == "network_roles"
    assert prompt.messages() == [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]
    assert prompt.context["alert_id"] == "2026494"
    assert prompt.context["detection"]["rule_code"] == "RPAADM_002635"
    assert prompt.context["evidence"]["primary_evidence_path"] == "alert.hitLog[0].zeusRawLogs[0]"
    assert "source_candidate_conflict" not in prompt.context["fact_reconstruction"]["conflict_types"]
    assert "field trust" in prompt.system
    assert "open-vocabulary" in prompt.system
    assert "detection_hit" in prompt.system
    assert "<ENCODED:...:OMITTED>" in prompt.system
    assert "does not reveal hidden bytes" in prompt.system
    assert "provider_detection_outcome_assertion" in prompt.system
    assert "role_coherence" in prompt.system
    assert "Alert admission is a trusted scoped fact" in prompt.system
    assert "Do not require another source to prove that the detection hit occurred" in prompt.system
    assert "Missing duplicate SYN, flow, PCAP, CMDB, endpoint, or tool corroboration" in prompt.system
    assert "documented routine omission is not automatically material" in prompt.system
    assert "Always give the best current verdict when optional enrichment is missing" in prompt.system
    assert "Do not copy evidence paths or values" in prompt.system
    assert "evidence" not in prompt.response_schema
    assert "knowledge_candidates" not in prompt.response_schema
    assert "general_security_knowledge" in prompt.system
    assert "Runtime restores stable IDs and creates R-* reasoning items" in prompt.system
    assert "Return exactly one JSON object" not in prompt.system
    assert prompt.user.startswith('<analysis_context trust="untrusted_evidence_data">')
    assert "<response_contract>" in prompt.user
    assert "<final_checklist>" in prompt.user
    assert "soc.analysis_model_output.v1" not in prompt.user
    assert prompt.context["skill_context"]["selected_skills"]
    assert prompt.context["skill_context"]["total_token_budget"] > 0
    assert "skill_context" in prompt.user
    assert "reviewed adapter semantics" in prompt.user
    evidence_refs = [item["evidence_ref"] for item in prompt.context["reference_catalogs"]["current_alert_evidence"] if "evidence_ref" in item]
    role_entities = prompt.context["reference_catalogs"]["role_entities"]
    assert evidence_refs[0] == "E-001"
    assert all(len(reference) == 5 for reference in evidence_refs)
    assert role_entities
    assert all(item["evidence_ref"] in evidence_refs for item in role_entities)
    assert all(item["entity_type"] for item in role_entities)
    assert "must be selected from reference_catalogs.role_entities" in prompt.system
    assert "copy only the item's evidence_ref into entity_ref" in prompt.system
    assert "Every scenario item has a non-empty rationale" in prompt.user
    assert "Each scenario item contains exactly these keys" in prompt.user
    assert "scenario_name is always present and non-empty" in prompt.user
    assert "network_direction always has a non-empty rationale" in prompt.user
    assert "network_direction contains exactly these keys" in prompt.user
    assert "role_adjudication always has a non-empty overall rationale" in prompt.user
    assert "role_adjudication contains exactly these keys" in prompt.user
    assert "Never output entity_type, value, connection_initiator" in prompt.user
    assert "Represent source/destination only in network_direction" in prompt.user
    assert "role_adjudication.conflicts contains only actual contradictory claims" in prompt.user
    assert 'never add prose such as "无冲突" as an array item' in prompt.user
    assert "Render Windows paths in generated prose with forward slashes" in prompt.user
    assert "Write every free-text value in concise Chinese" in prompt.user
    assert prompt.user.count("<output_example ") == 1
    assert 'id="network_roles"' in prompt.user
    assert "Never copy an EX-* reference into the answer" in prompt.user
    assert "Never reuse the example verdict, scenario, direction, roles" in prompt.user
    assert "Never emit an EX-* example reference" in prompt.user
    assert "never copy a conclusion value from the synthetic example" in prompt.user
    assert prompt.response_schema["role_adjudication"]["conflicts"] == ["actual contradictory role claim only; empty when no conflict exists"]
    assert prompt.context["model_reference_protocol"]["runtime_restores_stable_references"] is True


def test_analysis_prompt_keeps_long_context_before_tail_output_contract() -> None:
    prompt = build_analysis_prompt(_analysis_request("pingan_legacy_apt.json"))

    context_start = prompt.user.index("<analysis_context")
    context_end = prompt.user.index("</analysis_context>")
    task_start = prompt.user.index("<task>")
    example_start = prompt.user.index("<output_example")
    contract_start = prompt.user.index("<response_contract>")
    checklist_start = prompt.user.index("<final_checklist>")

    assert context_start == 0
    assert context_end < task_start < example_start < contract_start < checklist_start
    assert prompt.user.rstrip().endswith("</final_checklist>")
    assert json.dumps(prompt.response_schema, ensure_ascii=False, indent=2, sort_keys=True) in prompt.user
    assert json.dumps(prompt.response_schema, ensure_ascii=False, indent=2, sort_keys=True) not in prompt.system
    assert len(prompt.system) < 8_000
    assert "format_fragments" not in prompt.user


def test_analysis_prompt_selects_one_relevant_complete_example() -> None:
    non_network_prompt = build_analysis_prompt(_analysis_request("missing_fields.json"))
    conflicted_request = _analysis_request("pingan_legacy_apt.json").model_copy(
        update={
            "conflict_count": 1,
            "conflict_types": ["test_role_conflict"],
        }
    )
    conflicted_prompt = build_analysis_prompt(conflicted_request)

    assert non_network_prompt.example_id == "non_network"
    assert non_network_prompt.user.count("<output_example ") == 1
    assert 'id="non_network"' in non_network_prompt.user
    assert conflicted_prompt.example_id == "conflicted"
    assert conflicted_prompt.user.count("<output_example ") == 1
    assert 'id="conflicted"' in conflicted_prompt.user


def test_all_analysis_output_examples_pass_current_parser_contract() -> None:
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="10.0.0.10",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="10.0.0.20",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]
    replacements = {
        "EX-E-001": "E-001",
        "EX-E-002": "E-002",
    }

    def replace_example_references(value):
        if isinstance(value, dict):
            return {key: replace_example_references(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_example_references(item) for item in value]
        return replacements.get(value, value)

    examples = analysis_output_examples()
    assert set(examples) == {"network_roles", "non_network", "conflicted"}
    for example_id, example in examples.items():
        assert example["summary"]
        parsed = parse_analysis_result_output(
            json.dumps(replace_example_references(example), ensure_ascii=False),
            evidence_catalog=catalog,
        )
        assert parsed.repair_applied is False, example_id
        assert parsed.result.summary == example["summary"], example_id
        assert parsed.result.reason == example["reason"], example_id


def test_analysis_prompt_projects_only_selected_low_trust_structured_fallback() -> None:
    prompt = build_analysis_prompt(_analysis_request("pingan_legacy_edr.json"))
    user_prompt = prompt.user

    skill_names = [item["skill_name"] for item in prompt.context["skill_context"]["selected_skills"]]
    assert "soc-endpoint-triage" in skill_names
    assert prompt.context["evidence"]["selected_input_available"] is True
    assert prompt.context["evidence"]["evidence_policy"]["trust_level"] == "low"
    assert "evidence input policy selected low-trust structured fallback" in prompt.context["fact_reconstruction"]["warnings"]
    primary = prompt.context["evidence"]["primary_evidence"]
    assert primary["layer"] == "raw_structured"
    assert primary["source_path"] == "alert.hitLog[0].zeusRawLogs[0]"
    assert primary["content_format"] == "json"
    assert isinstance(primary["content"], dict)
    assert primary["projection"]["status"] == "complete_within_budget"
    assert primary["projection"]["visible_field_count"] > 0
    assert "process__cmd_line" in primary["content"]
    assert "finding__desc" in primary["content"]
    assert "relatedAlertList" not in primary["content"]
    assert "hitLog" not in primary["content"]
    assert "zeusRawLogs" in user_prompt


def test_analysis_prompt_exposes_readable_model_coverage_without_audit_paths() -> None:
    prompt = build_analysis_prompt(_analysis_request("pingan_legacy_hids.json"))

    coverage = prompt.context["evidence"]["coverage"]
    assert coverage["analysis_readiness"] == {
        "status": "ready",
        "summary": "当前主要证据已进入模型上下文；常规预算省略不代表关键证据缺失。",
        "high_value_gap_count": 0,
    }
    assert coverage["message_parsing"]["recognized_count"] == 1
    assert coverage["message_parsing"]["parsers"][0] == {
        "parser": "pingan_loose_kv",
        "status": "recognized",
        "parsed_field_count": 6,
        "warning_count": 0,
    }
    assert coverage["model_projection"]["visible_field_count"] == 6
    serialized = json.dumps(prompt.context, ensure_ascii=False)
    assert "schema_fingerprint" not in serialized
    assert "projected_field_paths" not in serialized
    assert "omitted_field_paths" not in serialized
    assert '"content": {' in prompt.user


def test_analysis_prompt_handles_missing_evidence_policy() -> None:
    prompt = build_analysis_prompt(_analysis_request("missing_fields.json"))

    assert prompt.context["evidence"]["primary_evidence_path"] is None
    assert prompt.context["evidence"]["evidence_policy"] is None
    assert "missing evidence input policy" in prompt.context["fact_reconstruction"]["warnings"]
    assert "needs_review" in prompt.system
    assert "knowledge_candidates" not in prompt.response_schema
    assert prompt.response_schema["schema_version"] == "soc.analysis_model_output.v4"
    assert "decision_evidence_refs" in prompt.response_schema
    assert "decision_context_refs" in prompt.response_schema
    assert "reasoning" not in prompt.response_schema
    assert "reasoning_refs" not in json.dumps(
        prompt.response_schema,
        ensure_ascii=False,
    )
    assert "scenario_assessments" in prompt.response_schema
    assert "network_direction" in prompt.response_schema
    assert "role_adjudication" in prompt.response_schema
    assert "organization-boundary direction" in prompt.system
    assert "Runtime derives action-specific targets" in prompt.system
    assert "evidence_gaps" in prompt.response_schema
    assert "manual_checks" in prompt.response_schema
