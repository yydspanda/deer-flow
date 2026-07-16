from __future__ import annotations

import json

import pytest

from soc_agent.contracts import Verdict
from soc_agent.llm import ANALYSIS_JSON_PARSER_VERSION, LLMOutputParseError, parse_analysis_result_output
from soc_agent.llm.json_parser import MAX_ANALYSIS_RESPONSE_CHARS


def _valid_payload() -> dict:
    return {
        "verdict": "suspicious",
        "confidence": 0.76,
        "summary": "存在可疑横向移动迹象，需要复核。",
        "evidence": [
            {
                "source": "fact_reconstruction",
                "description": "角色候选和进程行为支持可疑判断",
                "value": "svchost.exe",
            }
        ],
        "reason": "检测到远程注册表相关行为，但仍需要资产和历史上下文确认。",
        "recommended_action": "review_and_investigate",
        "knowledge_candidates": [],
    }


def test_parse_analysis_result_accepts_strict_json() -> None:
    parsed = parse_analysis_result_output(json.dumps(_valid_payload(), ensure_ascii=False))

    assert parsed.parser_version == ANALYSIS_JSON_PARSER_VERSION
    assert parsed.repair_applied is False
    assert parsed.result.verdict == Verdict.SUSPICIOUS
    assert parsed.result.confidence == 0.76


def test_parse_analysis_result_strips_think_and_code_fence() -> None:
    raw = "<think>这里有 { 干扰符号</think>\n```json\n" + json.dumps(_valid_payload(), ensure_ascii=False) + "\n```"

    parsed = parse_analysis_result_output(raw)

    assert parsed.repair_applied is False
    assert parsed.result.summary == "存在可疑横向移动迹象，需要复核。"


def test_parse_analysis_result_extracts_json_from_prose() -> None:
    raw = "下面是结论：\n" + json.dumps(_valid_payload(), ensure_ascii=False) + "\n请查收。"

    parsed = parse_analysis_result_output(raw)

    assert parsed.repair_applied is False
    assert parsed.result.recommended_action == "review_and_investigate"


def test_parse_analysis_result_repairs_trailing_comma() -> None:
    payload = json.dumps(_valid_payload(), ensure_ascii=False)
    raw = payload.replace('"knowledge_candidates": []', '"knowledge_candidates": [],')

    parsed = parse_analysis_result_output(raw)

    assert parsed.repair_applied is True
    assert parsed.result.verdict == Verdict.SUSPICIOUS


def test_parse_analysis_result_repairs_unquoted_keys() -> None:
    raw = """
    {
      verdict: suspicious,
      confidence: 0.76,
      summary: "存在可疑横向移动迹象，需要复核。",
      evidence: [{source: "fact_reconstruction", description: "命中可疑行为", value: "svchost.exe"}],
      reason: "检测到远程注册表相关行为，但仍需要资产和历史上下文确认。",
      recommended_action: "review_and_investigate",
      knowledge_candidates: []
    }
    """

    parsed = parse_analysis_result_output(raw)

    assert parsed.repair_applied is True
    assert parsed.repair_log
    assert parsed.result.evidence[0].source == "fact_reconstruction"


def test_parse_analysis_result_repairs_single_item_verdict_array() -> None:
    payload = _valid_payload()
    payload["verdict"] = ["suspicious"]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.repair_applied is True
    assert parsed.repair_log == [
        {
            "stage": "schema_normalization",
            "field": "verdict",
            "repair": "single_item_array_to_scalar",
        }
    ]
    assert parsed.result.verdict is Verdict.SUSPICIOUS


def test_parse_analysis_result_rejects_multi_item_verdict_array() -> None:
    payload = _valid_payload()
    payload["verdict"] = ["suspicious", "true_positive"]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"


def test_parse_analysis_result_repairs_single_item_evidence_value_array() -> None:
    payload = _valid_payload()
    payload["evidence"][0]["value"] = ["ASP.NET"]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.repair_applied is True
    assert parsed.repair_log == [
        {
            "stage": "schema_normalization",
            "field": "evidence[0].value",
            "repair": "single_item_array_to_scalar",
        }
    ]
    assert parsed.result.evidence[0].value == "ASP.NET"


def test_parse_analysis_result_serializes_multi_item_evidence_value_array() -> None:
    payload = _valid_payload()
    payload["evidence"][0]["value"] = ["ASP.NET", "PHP"]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.repair_applied is True
    assert parsed.result.evidence[0].value == '["ASP.NET","PHP"]'
    assert parsed.repair_log[-1]["repair"] == "structured_value_to_json_string"


def test_parse_analysis_result_serializes_evidence_value_object() -> None:
    payload = _valid_payload()
    payload["evidence"][0]["value"] = {"source_type": "hids", "product": "青藤"}

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.repair_applied is True
    assert parsed.result.evidence[0].value == '{"product":"青藤","source_type":"hids"}'
    assert parsed.repair_log[-1] == {
        "stage": "schema_normalization",
        "field": "evidence[0].value",
        "repair": "structured_value_to_json_string",
    }


def test_parse_analysis_result_rejects_oversized_structured_evidence_value() -> None:
    payload = _valid_payload()
    payload["evidence"][0]["value"] = {"content": "x" * 4_001}

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"


def test_parse_analysis_result_rejects_string_confidence() -> None:
    payload = _valid_payload()
    payload["confidence"] = "0.76"

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "confidence" in str(exc.value)


def test_parse_analysis_result_rejects_missing_evidence() -> None:
    payload = _valid_payload()
    payload["evidence"] = []

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "evidence" in str(exc.value)


def test_parse_analysis_result_rejects_unrecoverable_text() -> None:
    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output("not json at all")

    assert exc.value.stage == "json_repair"
    assert exc.value.repair_applied is True


def test_parse_analysis_result_rejects_oversized_output_before_repair() -> None:
    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output("{" + "x" * MAX_ANALYSIS_RESPONSE_CHARS + "}")

    assert exc.value.stage == "output_size"
