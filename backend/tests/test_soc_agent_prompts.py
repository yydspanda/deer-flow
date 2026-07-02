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
    assert "source_candidate_conflict" in prompt.context["fact_reconstruction"]["conflict_types"]
    assert "field-trust" in prompt.system
    assert "Return JSON only" in prompt.system
    assert "Bounded analysis context" in prompt.user


def test_analysis_prompt_shows_low_trust_fallback_without_dumping_raw_payload() -> None:
    prompt = build_analysis_prompt(_analysis_request("pingan_legacy_edr.json"))
    user_prompt = prompt.user

    assert prompt.context["evidence"]["selected_input_available"] is True
    assert prompt.context["evidence"]["evidence_policy"]["trust_level"] == "low"
    assert "evidence input policy selected low-trust structured fallback" in prompt.context["fact_reconstruction"]["warnings"]
    assert "process__cmd_line" not in user_prompt
    assert "finding__desc" not in user_prompt
    assert "zeusRawLogs" in user_prompt


def test_analysis_prompt_handles_missing_evidence_policy() -> None:
    prompt = build_analysis_prompt(_analysis_request("missing_fields.json"))

    assert prompt.context["evidence"]["primary_evidence_path"] is None
    assert prompt.context["evidence"]["evidence_policy"] is None
    assert "missing evidence input policy" in prompt.context["fact_reconstruction"]["warnings"]
    assert "needs_review" in prompt.system
    assert "knowledge_candidates" in prompt.response_schema
