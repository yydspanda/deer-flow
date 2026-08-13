from __future__ import annotations

import json
from pathlib import Path

from soc_agent.core import SocAnalysisService
from soc_agent.prompts import ANALYSIS_PROMPT_VERSION, build_analysis_prompt

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
    assert prompt.messages() == [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]
    assert prompt.context["alert_id"] == "2026494"
    assert prompt.context["detection"]["rule_code"] == "RPAADM_002635"
    assert prompt.context["evidence"]["primary_evidence_path"] == "alert.hitLog[0].zeusRawLogs[0]"
    assert "source_candidate_conflict" not in prompt.context["fact_reconstruction"]["conflict_types"]
    assert "field-trust" in prompt.system
    assert "open vocabulary" in prompt.system
    assert "detection_hit" in prompt.system
    assert "<ENCODED:...:OMITTED>" in prompt.system
    assert "marker-bearing scalar" in prompt.system
    assert "does not reveal the hidden bytes" in prompt.system
    assert "provider_detection_outcome_assertion" in prompt.system
    assert "Do not copy evidence source paths or values" in prompt.system
    assert "evidence" not in prompt.response_schema
    assert "knowledge_candidates" not in prompt.response_schema
    assert "general_security_knowledge" in prompt.system
    assert "Runtime materializes source paths and values" in prompt.system
    assert "Return JSON only" in prompt.system
    assert "Bounded analysis context" in prompt.user
    assert "Required JSON response schema" not in prompt.user
    assert "soc.analysis_model_output.v1" not in prompt.user
    assert prompt.context["skill_context"]["selected_skills"]
    assert prompt.context["skill_context"]["total_token_budget"] > 0
    assert "skill_context" in prompt.user


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
    assert "process__cmd_line" in primary["content"]
    assert "finding__desc" in primary["content"]
    assert "relatedAlertList" not in primary["content"]
    assert "hitLog" not in primary["content"]
    assert "zeusRawLogs" in user_prompt


def test_analysis_prompt_handles_missing_evidence_policy() -> None:
    prompt = build_analysis_prompt(_analysis_request("missing_fields.json"))

    assert prompt.context["evidence"]["primary_evidence_path"] is None
    assert prompt.context["evidence"]["evidence_policy"] is None
    assert "missing evidence input policy" in prompt.context["fact_reconstruction"]["warnings"]
    assert "needs_review" in prompt.system
    assert "knowledge_candidates" not in prompt.response_schema
    assert prompt.response_schema["schema_version"] == "soc.analysis_model_output.v1"
    assert "reasoning" in prompt.response_schema
    assert "scenario_assessments" in prompt.response_schema
    assert "network_direction" in prompt.response_schema
    assert "role_adjudication" in prompt.response_schema
    assert "organization-boundary direction" in prompt.system
    assert "automation_allowed=false" in prompt.system
    assert "evidence_gaps" in prompt.response_schema
    assert "manual_checks" in prompt.response_schema
