from __future__ import annotations

import json

import pytest

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisEvidenceCatalogItem,
    Verdict,
)
from soc_agent.llm import ANALYSIS_JSON_PARSER_VERSION, LLMOutputParseError, parse_analysis_result_output
from soc_agent.llm.json_parser import MAX_ANALYSIS_RESPONSE_CHARS


def _valid_payload() -> dict:
    return {
        "schema_version": "soc.analysis_result.v4",
        "verdict": "suspicious",
        "confidence": 0.76,
        "summary": "存在可疑横向移动迹象，需要复核。",
        "evidence": [
            {
                "evidence_ref": "E-A1B2C3D4E5F6",
                "source": "fact_reconstruction",
                "description": "角色候选和进程行为支持可疑判断",
                "value": "svchost.exe",
            }
        ],
        "reasoning": [
            {
                "schema_version": "soc.analysis_reasoning_item.v1",
                "reasoning_id": "R-01",
                "statement": "该进程行为与远程服务横向移动的常见模式相符。",
                "basis": ["current_evidence", "general_security_knowledge"],
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "context_refs": [],
                "confidence": 0.71,
            }
        ],
        "scenario_assessments": [
            {
                "schema_version": "soc.triage_scenario_assessment.v2",
                "scenario_name": "远程服务横向移动",
                "scenario_key": "remote_service_lateral_movement",
                "is_primary": True,
                "origin": "inferred",
                "confidence": 0.71,
                "activity_stage": "attempt_observed",
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "reasoning_refs": ["R-01"],
                "rationale": "进程行为与远程服务使用相符，但缺少目标侧执行结果。",
                "competing_explanations": ["授权远程运维"],
            }
        ],
        "network_direction": {
            "schema_version": "soc.network_direction_assessment.v1",
            "status": "indeterminate",
            "observed_flow": "not_available",
            "boundary_direction": "not_applicable",
            "semantic_direction": None,
            "connection_initiator": None,
            "intermediaries": [],
            "confidence": 0.4,
            "evidence_refs": ["E-A1B2C3D4E5F6"],
            "reasoning_refs": ["R-01"],
            "context_refs": [],
            "rationale": "该样本没有网络流证据。",
            "evidence_gaps": ["缺少网络连接元组。"],
        },
        "role_adjudication": {
            "schema_version": "soc.role_adjudication_result.v1",
            "status": "tentative",
            "roles": [
                {
                    "role": "impacted_asset",
                    "entity_type": "process",
                    "value": "svchost.exe",
                    "status": "tentative",
                    "confidence": 0.6,
                    "evidence_refs": ["E-A1B2C3D4E5F6"],
                    "reasoning_refs": ["R-01"],
                    "context_refs": [],
                    "rationale": "进程是当前可见的调查对象。",
                }
            ],
            "response_target_proposals": [
                {
                    "proposal_id": "RT-01",
                    "action_kind": "investigate_process",
                    "target_type": "process",
                    "target_value": "svchost.exe",
                    "target_role": "impacted_asset",
                    "confidence": 0.6,
                    "evidence_refs": ["E-A1B2C3D4E5F6"],
                    "reasoning_refs": ["R-01"],
                    "context_refs": [],
                    "rationale": "先调查该进程，不表示已经执行响应。",
                    "policy_review_required": True,
                    "automation_allowed": False,
                }
            ],
            "conflicts": [],
            "evidence_gaps": ["缺少目标主机上下文。"],
            "rationale": "当前只可暂定调查对象。",
        },
        "evidence_gaps": ["缺少目标主机进程树和登录结果。"],
        "manual_checks": ["查询目标主机同时间窗的登录事件和子进程。"],
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


def test_parse_analysis_result_keeps_direction_roles_and_action_specific_targets() -> None:
    payload = _valid_payload()
    payload["reasoning"][0]["basis"] = [
        "current_evidence",
        "governed_context",
    ]
    payload["reasoning"][0]["context_refs"] = ["C-ABCDEF123456"]
    payload["network_direction"] = {
        "schema_version": "soc.network_direction_assessment.v1",
        "status": "inferred",
        "observed_flow": "source_to_destination",
        "boundary_direction": "internal_to_internal",
        "semantic_direction": "victim_to_attacker_reverse_connection",
        "connection_initiator": "30.116.114.150",
        "intermediaries": [],
        "confidence": 0.86,
        "evidence_refs": ["E-A1B2C3D4E5F6"],
        "reasoning_refs": ["R-01"],
        "context_refs": ["C-ABCDEF123456"],
        "rationale": "反向连接中，线上的发起者可以是受害主机。",
        "evidence_gaps": [],
    }
    payload["role_adjudication"] = {
        "schema_version": "soc.role_adjudication_result.v1",
        "status": "resolved_from_evidence",
        "roles": [
            {
                "role": "victim",
                "entity_type": "ip",
                "value": "30.116.114.150",
                "status": "resolved_from_evidence",
                "confidence": 0.86,
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "reasoning_refs": ["R-01"],
                "context_refs": ["C-ABCDEF123456"],
                "rationale": "该实体主动回连，但语义角色为失陷主机。",
            },
            {
                "role": "c2",
                "entity_type": "ip",
                "value": "30.174.29.44",
                "status": "resolved_from_evidence",
                "confidence": 0.82,
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "reasoning_refs": ["R-01"],
                "context_refs": ["C-ABCDEF123456"],
                "rationale": "该实体是反向连接监听端候选。",
            },
        ],
        "response_target_proposals": [
            {
                "proposal_id": "RT-01",
                "action_kind": "isolate_host",
                "target_type": "ip",
                "target_value": "30.116.114.150",
                "target_role": "victim",
                "confidence": 0.86,
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "reasoning_refs": ["R-01"],
                "context_refs": ["C-ABCDEF123456"],
                "rationale": "隔离目标应是失陷主机，而不是可见连接的目的端。",
                "policy_review_required": True,
                "automation_allowed": False,
            }
        ],
        "conflicts": [],
        "evidence_gaps": [],
        "rationale": "角色与动作目标分开表达。",
    }

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.result.network_direction.semantic_direction == "victim_to_attacker_reverse_connection"
    assert parsed.result.role_adjudication.roles[0].role.value == "victim"
    target = parsed.result.role_adjudication.response_target_proposals[0]
    assert target.action_kind == "isolate_host"
    assert target.target_value == "30.116.114.150"
    assert target.automation_allowed is False


def test_parse_analysis_result_rejects_target_without_matching_role() -> None:
    payload = _valid_payload()
    payload["role_adjudication"]["response_target_proposals"][0].update(
        {
            "target_role": "attacker",
            "target_value": "198.51.100.44",
        }
    )

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "must reference an adjudicated role" in str(exc.value)


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
      schema_version: "soc.analysis_result.v4",
      verdict: suspicious,
      confidence: 0.76,
      summary: "存在可疑横向移动迹象，需要复核。",
      evidence: [{evidence_ref: "E-A1B2C3D4E5F6", source: "fact_reconstruction", description: "命中可疑行为", value: "svchost.exe"}],
      reasoning: [{
        schema_version: "soc.analysis_reasoning_item.v1",
        reasoning_id: "R-01",
        statement: "该行为与远程服务横向移动模式相符。",
        basis: [current_evidence, general_security_knowledge],
        evidence_refs: ["E-A1B2C3D4E5F6"],
        context_refs: [],
        confidence: 0.71
      }],
      scenario_assessments: [{
        schema_version: "soc.triage_scenario_assessment.v2",
        scenario_name: "远程服务横向移动",
        scenario_key: "remote_service_lateral_movement",
        is_primary: true,
        origin: inferred,
        confidence: 0.71,
        activity_stage: attempt_observed,
        evidence_refs: ["E-A1B2C3D4E5F6"],
        reasoning_refs: ["R-01"],
        rationale: "进程行为与远程服务使用相符。",
        competing_explanations: ["授权远程运维"]
      }],
      network_direction: {
        schema_version: "soc.network_direction_assessment.v1",
        status: "indeterminate",
        observed_flow: "not_available",
        boundary_direction: "not_applicable",
        semantic_direction: null,
        connection_initiator: null,
        intermediaries: [],
        confidence: 0.4,
        evidence_refs: ["E-A1B2C3D4E5F6"],
        reasoning_refs: ["R-01"],
        context_refs: [],
        rationale: "该样本没有网络流证据。",
        evidence_gaps: ["缺少网络连接元组。"]
      },
      role_adjudication: {
        schema_version: "soc.role_adjudication_result.v1",
        status: "tentative",
        roles: [{
          role: "impacted_asset",
          entity_type: "process",
          value: "svchost.exe",
          status: "tentative",
          confidence: 0.6,
          evidence_refs: ["E-A1B2C3D4E5F6"],
          reasoning_refs: ["R-01"],
          context_refs: [],
          rationale: "进程是当前可见的调查对象。"
        }],
        response_target_proposals: [],
        conflicts: [],
        evidence_gaps: ["缺少目标主机上下文。"],
        rationale: "当前只可暂定调查对象。"
      },
      evidence_gaps: ["缺少目标主机进程树。"],
      manual_checks: ["查询目标主机同时间窗的进程树。"],
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


def test_parse_analysis_result_rejects_missing_d7_contract_fields() -> None:
    payload = _valid_payload()
    del payload["manual_checks"]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "manual_checks" in str(exc.value)


def test_parse_analysis_result_rejects_scenario_without_one_primary() -> None:
    payload = _valid_payload()
    payload["scenario_assessments"][0]["is_primary"] = False

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "exactly one primary" in str(exc.value)


def test_parse_analysis_result_rejects_unknown_scenario_evidence_reference() -> None:
    payload = _valid_payload()
    payload["scenario_assessments"][0]["evidence_refs"] = ["E-000000000000"]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "scenario evidence_refs" in str(exc.value)
    assert "invalid refs" in str(exc.value)


def test_parse_analysis_result_allows_unknown_scenario_with_explicit_gap() -> None:
    payload = _valid_payload()
    payload["scenario_assessments"] = []

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.result.scenario_assessments == []
    assert parsed.result.evidence_gaps
    assert parsed.result.manual_checks


def test_parse_analysis_result_accepts_typed_inert_knowledge_candidate() -> None:
    payload = _valid_payload()
    payload["knowledge_candidates"] = [
        {
            "schema_version": "soc.analysis_knowledge_candidate.v1",
            "candidate_id": "K-01",
            "statement": "远程服务告警应结合授权变更记录复核。",
            "destination_hint": "general_skill",
            "scope_hint": "global",
            "evidence_refs": ["E-A1B2C3D4E5F6"],
            "reasoning_refs": ["R-01"],
            "rationale": "该核查步骤可跨租户复用，但仍需人工审核。",
        }
    ]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    candidate = parsed.result.knowledge_candidates[0]
    assert candidate.candidate_id == "K-01"
    assert candidate.destination_hint.value == "general_skill"


def test_parse_analysis_result_rejects_unresolved_knowledge_candidate_reference() -> None:
    payload = _valid_payload()
    payload["knowledge_candidates"] = [
        {
            "schema_version": "soc.analysis_knowledge_candidate.v1",
            "candidate_id": "K-01",
            "statement": "未经当前结果支撑的候选。",
            "destination_hint": "reject_or_verify",
            "scope_hint": "event",
            "evidence_refs": ["E-000000000000"],
            "reasoning_refs": ["R-01"],
            "rationale": "测试引用完整性。",
        }
    ]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "knowledge candidate references" in str(exc.value)


def test_parse_analysis_result_safely_normalizes_inert_candidate_hints() -> None:
    payload = _valid_payload()
    payload["knowledge_candidates"] = [
        {
            "schema_version": "soc.analysis_knowledge_candidate.v1",
            "candidate_id": "K-01",
            "statement": "该建议需要人工决定最终落点。",
            "destination_hint": "detection",
            "scope_hint": "adapter",
            "evidence_refs": ["E-A1B2C3D4E5F6"],
            "reasoning_refs": ["R-01"],
            "rationale": "候选元数据不能阻断核心研判。",
        }
    ]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    candidate = parsed.result.knowledge_candidates[0]
    assert parsed.repair_applied is True
    assert candidate.destination_hint.value == "reject_or_verify"
    assert candidate.scope_hint == "provider"
    assert [item["stage"] for item in parsed.repair_log] == [
        "candidate_hint_normalization",
        "candidate_hint_normalization",
    ]


def test_parse_analysis_result_repairs_reference_only_from_exact_catalog_fact() -> None:
    payload = _valid_payload()
    truncated = "E-A1B2C3D4E5F"
    payload["evidence"][0]["evidence_ref"] = truncated
    payload["reasoning"][0]["evidence_refs"] = [truncated]
    payload["scenario_assessments"][0]["evidence_refs"] = [truncated]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="fact_reconstruction",
            value="svchost.exe",
            value_type="string",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.result.evidence[0].evidence_ref == "E-A1B2C3D4E5F6"
    assert parsed.result.reasoning[0].evidence_refs == ["E-A1B2C3D4E5F6"]
    assert parsed.result.scenario_assessments[0].evidence_refs == ["E-A1B2C3D4E5F6"]
    assert any(item["repair"] == "exact_catalog_fact_to_reference" for item in parsed.repair_log)


def test_parse_analysis_result_materializes_valid_catalog_fact_cited_only_by_reasoning() -> None:
    payload = _valid_payload()
    payload["reasoning"][0]["evidence_refs"].append("E-111111111111")
    payload["scenario_assessments"][0]["evidence_refs"].append("E-111111111111")
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="fact_reconstruction",
            value="svchost.exe",
            value_type="string",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-111111111111",
            source_path="entities.process.command_line",
            value="reg add HKLM\\SYSTEM\\CurrentControlSet\\Services",
            value_type="string",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is True
    assert [item.evidence_ref for item in parsed.result.evidence] == [
        "E-A1B2C3D4E5F6",
        "E-111111111111",
    ]
    materialized = parsed.result.evidence[1]
    assert materialized.source == "entities.process.command_line"
    assert materialized.value == "reg add HKLM\\SYSTEM\\CurrentControlSet\\Services"
    assert any(item["repair"] == "materialize_referenced_catalog_facts" for item in parsed.repair_log)


def test_parse_analysis_result_accepts_bounded_evidence_list_over_twenty_items() -> None:
    payload = _valid_payload()
    payload["evidence"] = [
        {
            "evidence_ref": f"E-{index:012X}",
            "source": f"facts[{index}]",
            "description": "Observed current-alert fact",
            "value": index,
        }
        for index in range(22)
    ]
    payload["reasoning"][0]["evidence_refs"] = ["E-000000000000"]
    payload["scenario_assessments"][0]["evidence_refs"] = ["E-000000000000"]
    payload["network_direction"]["evidence_refs"] = ["E-000000000000"]
    payload["role_adjudication"]["roles"][0]["evidence_refs"] = ["E-000000000000"]
    payload["role_adjudication"]["response_target_proposals"][0]["evidence_refs"] = ["E-000000000000"]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert len(parsed.result.evidence) == 22


def test_parse_analysis_result_removes_exact_duplicate_evidence_reference() -> None:
    payload = _valid_payload()
    duplicate = dict(payload["evidence"][0])
    duplicate["description"] = "Same fact repeated with another observation label"
    payload["evidence"].append(duplicate)

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.repair_applied is True
    assert len(parsed.result.evidence) == 1
    assert any(item["repair"] == "remove_exact_duplicate_evidence_refs" for item in parsed.repair_log)


def test_parse_analysis_result_rebinds_duplicate_reference_from_exact_catalog_tuple() -> None:
    payload = _valid_payload()
    payload["evidence"].append(
        {
            "evidence_ref": "E-A1B2C3D4E5F6",
            "source": "canonical_entities.host.ip",
            "description": "Impacted endpoint IP",
            "value": "10.28.53.42",
        }
    )
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="fact_reconstruction",
            value="svchost.exe",
            value_type="string",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-111111111111",
            source_path="canonical_entities.host.ip",
            value="10.28.53.42",
            value_type="string",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert [item.evidence_ref for item in parsed.result.evidence] == [
        "E-A1B2C3D4E5F6",
        "E-111111111111",
    ]
    assert any(item["repair"] == "exact_catalog_fact_to_reference" and item["field"] == "evidence[1].evidence_ref" for item in parsed.repair_log)


def test_parse_analysis_result_does_not_merge_conflicting_duplicate_evidence() -> None:
    payload = _valid_payload()
    duplicate = dict(payload["evidence"][0])
    duplicate["value"] = "different.exe"
    payload["evidence"].append(duplicate)

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "evidence_refs must be unique" in str(exc.value)


@pytest.mark.parametrize(("raw_value", "expected"), [("true", True), ("FALSE", False)])
def test_parse_analysis_result_repairs_json_boolean_string_for_primary_scenario(
    raw_value: str,
    expected: bool,
) -> None:
    payload = _valid_payload()
    payload["scenario_assessments"][0]["is_primary"] = raw_value
    if expected is False:
        payload["scenario_assessments"] = []
        payload["scenario_assessments"].append(
            {
                **_valid_payload()["scenario_assessments"][0],
                "is_primary": raw_value,
            }
        )

    if expected is False:
        with pytest.raises(LLMOutputParseError) as exc:
            parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))
        assert "exactly one primary" in str(exc.value)
        return

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.result.scenario_assessments[0].is_primary is expected
    assert any(item["repair"] == "json_boolean_string_to_boolean" for item in parsed.repair_log)


def test_parse_analysis_result_marks_missing_scenario_rationale_without_inference() -> None:
    payload = _valid_payload()
    del payload["scenario_assessments"][0]["rationale"]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.result.scenario_assessments[0].rationale == ("Model omitted a separate scenario rationale; rely only on the cited E-* facts and R-* reasoning.")
    assert any(item["repair"] == "missing_redundant_rationale_to_explicit_placeholder" for item in parsed.repair_log)


def test_parse_analysis_result_keeps_missing_scenario_rationale_strict_without_refs() -> None:
    payload = _valid_payload()
    del payload["scenario_assessments"][0]["rationale"]
    payload["scenario_assessments"][0]["reasoning_refs"] = []

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"


def test_parse_analysis_result_removes_empty_context_reference_sentinel() -> None:
    payload = _valid_payload()
    payload["reasoning"][0]["context_refs"] = ["none", None, "不适用"]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.repair_applied is True
    assert parsed.result.reasoning[0].context_refs == []
    assert any(item["repair"] == "remove_empty_context_reference_sentinels" for item in parsed.repair_log)


def test_parse_analysis_result_removes_exact_duplicate_reasoning_references() -> None:
    payload = _valid_payload()
    payload["reasoning"][0]["evidence_refs"] = [
        "E-A1B2C3D4E5F6",
        "E-A1B2C3D4E5F6",
    ]

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.result.reasoning[0].evidence_refs == ["E-A1B2C3D4E5F6"]
    assert any(item["repair"] == "remove_exact_duplicate_references" and item["field"] == "reasoning[0].evidence_refs" for item in parsed.repair_log)


def test_parse_analysis_result_keeps_invalid_nonempty_context_reference_strict() -> None:
    payload = _valid_payload()
    payload["reasoning"][0]["context_refs"] = ["current alert only"]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "context_refs must use" in str(exc.value)


def test_parse_analysis_result_derives_basis_from_explicit_context_reference() -> None:
    payload = _valid_payload()
    payload["reasoning"][0]["context_refs"] = ["S-111111111111"]
    context_catalog = [
        AnalysisContextCatalogItem(
            context_ref="S-111111111111",
            kind="skill",
            label="soc-endpoint-triage",
            source_id="references/runtime-guidance.md",
            summary="Reviewed endpoint triage guidance.",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        context_catalog=context_catalog,
    )

    assert parsed.repair_applied is True
    assert [item.value for item in parsed.result.reasoning[0].basis] == [
        "current_evidence",
        "general_security_knowledge",
        "skill",
    ]
    assert any(item["repair"] == "derive_basis_from_explicit_context_refs" for item in parsed.repair_log)


def test_parse_analysis_result_does_not_guess_ambiguous_catalog_reference() -> None:
    payload = _valid_payload()
    payload["evidence"][0].update(
        {
            "evidence_ref": "E-A1B2C3D4E5F",
            "source": "unknown.path",
            "value": "unknown",
        }
    )
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref=reference,
            source_path=f"facts[{index}]",
            value=f"value-{index}",
            value_type="string",
        )
        for index, reference in enumerate(("E-A1B2C3D4E5F6", "E-A1B2C3D4E5FA"))
    ]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(
            json.dumps(payload, ensure_ascii=False),
            evidence_catalog=catalog,
        )

    assert exc.value.stage == "schema_validation"
    assert "evidence_ref" in str(exc.value)


def test_parse_analysis_result_rejects_unknown_scenario_without_gap() -> None:
    payload = _valid_payload()
    payload["scenario_assessments"] = []
    payload["evidence_gaps"] = []

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "domain_validation"
    assert "evidence gap" in str(exc.value)


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
