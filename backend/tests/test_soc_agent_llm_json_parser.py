from __future__ import annotations

import json

import pytest

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisEvidenceCatalogItem,
    AnalysisOutputSection,
    FactReconstructionResult,
    LLMAnalysisRequest,
    RoleCoherenceAssessment,
    RoleCoherenceRelationship,
    RoleCoherenceRelationshipStatus,
    RoleCoherenceStatus,
    Verdict,
)
from soc_agent.llm import ANALYSIS_JSON_PARSER_VERSION, LLMOutputParseError, parse_analysis_result_output
from soc_agent.llm.json_parser import (
    MAX_ANALYSIS_RESPONSE_CHARS,
    parse_analysis_section_patch_output,
    recover_analysis_result_output,
)


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


def _compact_payload() -> dict:
    payload = _valid_payload()
    payload["schema_version"] = "soc.analysis_model_output.v1"
    payload.pop("evidence")
    payload.pop("knowledge_candidates")
    for item in payload["reasoning"]:
        item.pop("schema_version")
    for item in payload["scenario_assessments"]:
        item.pop("schema_version")
    payload["network_direction"].pop("schema_version")
    payload["role_adjudication"].pop("schema_version")
    for proposal in payload["role_adjudication"]["response_target_proposals"]:
        proposal.pop("proposal_id")
        proposal.pop("policy_review_required")
        proposal.pop("automation_allowed")
    return payload


def _compact_v2_payload() -> dict:
    return {
        "schema_version": "soc.analysis_model_output.v2",
        "verdict": "true_positive",
        "confidence": 0.91,
        "summary": "当前连接符合反向连接行为。",
        "decision_evidence_refs": [
            "E-A1B2C3D4E5F6",
            "E-B1C2D3E4F5A6",
        ],
        "decision_context_refs": [],
        "reasoning": [
            {
                "reasoning_id": "R-01",
                "statement": "反向连接中，受害主机可以是网络连接发起方。",
                "basis": ["current_evidence", "general_security_knowledge"],
                "evidence_refs": [
                    "E-A1B2C3D4E5F6",
                    "E-B1C2D3E4F5A6",
                ],
                "context_refs": [],
                "confidence": 0.9,
            }
        ],
        "scenario_assessments": [
            {
                "scenario_name": "反向连接",
                "scenario_key": "reverse_connection",
                "is_primary": True,
                "origin": "inferred",
                "confidence": 0.9,
                "activity_stage": "effect_observed",
                "evidence_refs": [
                    "E-A1B2C3D4E5F6",
                    "E-B1C2D3E4F5A6",
                ],
                "reasoning_refs": ["R-00", "R-01"],
                "rationale": "端点角色与反向连接语义一致。",
                "competing_explanations": [],
            }
        ],
        "network_direction": {
            "status": "observed",
            "observed_flow": "source_to_destination",
            "boundary_direction": "internal_to_internal",
            "semantic_direction": "victim_to_attacker_callback",
            "connection_initiator_ref": "E-A1B2C3D4E5F6",
            "intermediaries": [],
            "confidence": 0.9,
            "evidence_refs": [
                "E-A1B2C3D4E5F6",
                "E-B1C2D3E4F5A6",
            ],
            "reasoning_refs": ["R-00", "R-01"],
            "context_refs": [],
            "rationale": "网络发起方与语义攻击者不是同一角色。",
            "evidence_gaps": [],
        },
        "role_adjudication": {
            "status": "resolved_from_evidence",
            "roles": [
                {
                    "role": "victim",
                    "entity_ref": "E-A1B2C3D4E5F6",
                    "status": "resolved_from_evidence",
                    "confidence": 0.9,
                    "evidence_refs": ["E-A1B2C3D4E5F6"],
                    "reasoning_refs": ["R-00", "R-01"],
                    "context_refs": [],
                    "rationale": "连接发起方是反向连接中的受害主机。",
                },
                {
                    "role": "attacker",
                    "entity_ref": "E-B1C2D3E4F5A6",
                    "status": "resolved_from_evidence",
                    "confidence": 0.9,
                    "evidence_refs": ["E-B1C2D3E4F5A6"],
                    "reasoning_refs": ["R-00", "R-01"],
                    "context_refs": [],
                    "rationale": "连接响应方是反向连接中的攻击者。",
                },
            ],
            "conflicts": [],
            "evidence_gaps": [],
            "rationale": "角色由当前告警事实和反向连接语义共同确定。",
        },
        "evidence_gaps": [],
        "manual_checks": [],
        "reason": "规则命中与两个端点事实共同支持反向连接结论。",
        "recommended_action": "按攻击者与受害者角色继续处置",
    }


def _compact_v3_payload() -> dict:
    payload = json.loads(json.dumps(_compact_v2_payload()))
    payload["schema_version"] = "soc.analysis_model_output.v3"
    payload.pop("reasoning")
    for scenario in payload["scenario_assessments"]:
        scenario.pop("reasoning_refs")
    payload["network_direction"].pop("reasoning_refs")
    for role in payload["role_adjudication"]["roles"]:
        role.pop("reasoning_refs")
    return payload


def _compact_v4_payload() -> dict:
    payload = json.loads(json.dumps(_compact_v3_payload()))
    payload["schema_version"] = "soc.analysis_model_output.v4"
    aliases = {
        "E-A1B2C3D4E5F6": "E-001",
        "E-B1C2D3E4F5A6": "E-002",
    }

    def replace(value):
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return aliases.get(value, value)

    return replace(payload)


def test_parse_compact_model_output_v4_restores_short_reference_aliases() -> None:
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]
    context_catalog = [
        AnalysisContextCatalogItem(
            context_ref="C-C1D2E3F4A5B6",
            kind="governed_context",
            label="internal network scope",
            source_id="tenant-knowledge:test",
            summary="Both endpoints are organization controlled.",
        )
    ]
    payload = _compact_v4_payload()
    payload["decision_context_refs"] = ["C-001"]
    payload["network_direction"]["context_refs"] = ["C-001"]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
        context_catalog=context_catalog,
    )

    assert parsed.repair_applied is False
    assert parsed.model_output_schema_version == "soc.analysis_model_output.v4"
    assert parsed.result.decision_evidence_refs == [
        "E-A1B2C3D4E5F6",
        "E-B1C2D3E4F5A6",
    ]
    assert parsed.result.network_direction.connection_initiator == "30.116.114.150"
    assert parsed.result.network_direction.context_refs == ["C-C1D2E3F4A5B6"]
    assert parsed.result.reasoning[0].context_refs == ["C-C1D2E3F4A5B6"]
    assert {role.role.value: role.value for role in parsed.result.role_adjudication.roles} == {
        "victim": "30.116.114.150",
        "attacker": "30.174.29.44",
    }
    alias_hydration = next(item for item in parsed.hydration_log if item.get("operation") == "restore_model_reference_aliases")
    assert alias_hydration["rewrite_count"] > 2


def test_parse_compact_model_output_v4_copies_reason_into_missing_summary() -> None:
    payload = _compact_v4_payload()
    payload.pop("summary")
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is True
    assert parsed.result.summary == payload["reason"]
    summary_hydration = next(item for item in parsed.hydration_log if item.get("operation") == "materialize_summary_from_reason")
    assert summary_hydration == {
        "stage": "runtime_hydration",
        "operation": "materialize_summary_from_reason",
        "field": "summary",
        "source_field": "reason",
        "exact_copy": True,
    }


def test_parse_compact_model_output_v4_does_not_truncate_reason_for_summary() -> None:
    payload = _compact_v4_payload()
    payload.pop("summary")
    payload["reason"] = "x" * 4_001
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(
            json.dumps(payload, ensure_ascii=False),
            evidence_catalog=catalog,
        )

    assert exc.value.stage == "model_output_core_validation"
    assert exc.value.field_paths == ("summary",)


def test_parse_compact_model_output_v4_rejects_unknown_short_core_alias() -> None:
    payload = _compact_v4_payload()
    payload["decision_evidence_refs"] = ["E-999"]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(
            json.dumps(payload, ensure_ascii=False),
            evidence_catalog=catalog,
        )

    assert exc.value.stage == "model_output_core_validation"
    assert exc.value.field_paths == ("decision_evidence_refs",)


def test_v4_materializes_missing_optional_rationale_from_explicit_object_fields() -> None:
    payload = _compact_v4_payload()
    payload["scenario_assessments"][0].pop("rationale")
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is True
    assert parsed.result.scenario_assessments[0].rationale == ("Model assessed scenario '反向连接' at activity stage 'effect_observed' using the cited evidence.")
    assert any(item.get("operation") == "materialize_missing_optional_rationale" for item in parsed.hydration_log)


def test_v4_moves_scenario_context_refs_into_runtime_reasoning() -> None:
    payload = _compact_v4_payload()
    payload["scenario_assessments"][0]["context_refs"] = ["C-001"]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]
    context_catalog = [
        AnalysisContextCatalogItem(
            context_ref="C-C1D2E3F4A5B6",
            kind="governed_context",
            label="reviewed network scope",
            source_id="tenant-knowledge:test",
            summary="Both endpoints are organization controlled.",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
        context_catalog=context_catalog,
    )

    scenario = parsed.result.scenario_assessments[0]
    reasoning = next(item for item in parsed.result.reasoning if item.reasoning_id == scenario.reasoning_refs[0])
    assert reasoning.context_refs == ["C-C1D2E3F4A5B6"]


def test_v4_bounds_optional_context_refs_to_unique_catalog_values() -> None:
    payload = _compact_v4_payload()
    context_catalog = [
        AnalysisContextCatalogItem(
            context_ref=f"C-{index:012X}",
            kind="governed_context",
            label=f"context {index}",
            source_id=f"tenant-knowledge:{index}",
            summary=f"Reviewed context {index}.",
        )
        for index in range(1, 22)
    ]
    payload["scenario_assessments"][0]["context_refs"] = [
        *(item.context_ref for item in context_catalog),
        context_catalog[0].context_ref,
        "none",
    ]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
        context_catalog=context_catalog,
    )

    scenario = parsed.result.scenario_assessments[0]
    reasoning = next(item for item in parsed.result.reasoning if item.reasoning_id == scenario.reasoning_refs[0])
    assert reasoning.context_refs == [item.context_ref for item in context_catalog[:20]]
    normalization = next(item for item in parsed.hydration_log if item.get("operation") == "retain_catalog_backed_optional_context_refs")
    assert normalization["retained_count"] == 20
    assert normalization["duplicate_ref_count"] == 1
    assert normalization["invalid_ref_count"] == 1
    assert normalization["truncated_ref_count"] == 1


def test_v4_uses_explicit_scenario_key_when_display_name_is_missing() -> None:
    payload = _compact_v4_payload()
    scenario = payload["scenario_assessments"][0]
    scenario.pop("scenario_name")
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.result.scenario_assessments[0].scenario_name == "reverse_connection"
    assert any(item.get("operation") == "materialize_scenario_name_from_key" for item in parsed.hydration_log)


def test_v4_marks_model_scenario_as_inferred_when_origin_is_missing() -> None:
    payload = _compact_v4_payload()
    payload["scenario_assessments"][0].pop("origin")
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.result.scenario_assessments[0].origin.value == "inferred"
    assert any(item.get("operation") == "materialize_conservative_scenario_origin" for item in parsed.hydration_log)


def test_v4_bounds_core_decision_evidence_refs_before_validation() -> None:
    payload = _compact_v4_payload()
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref=f"E-{index:012X}",
            source_path=f"canonical_entities.generic.value_{index}",
            value=f"value-{index}",
            value_type="string",
            trust_level="high",
        )
        for index in range(1, 22)
    ]
    payload["decision_evidence_refs"] = [
        *(item.evidence_ref for item in catalog),
        catalog[0].evidence_ref,
        "E-FFFFFFFFFFFF",
    ]
    payload["scenario_assessments"] = []
    payload.pop("network_direction")
    payload.pop("role_adjudication")

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.result.decision_evidence_refs == [item.evidence_ref for item in catalog[:20]]
    normalization = next(item for item in parsed.hydration_log if item.get("operation") == "retain_catalog_backed_core_evidence_refs")
    assert normalization["retained_count"] == 20
    assert normalization["duplicate_ref_count"] == 1
    assert normalization["invalid_ref_count"] == 1
    assert normalization["truncated_ref_count"] == 1


def test_v4_derives_missing_role_entity_only_from_one_unique_cited_value() -> None:
    payload = _compact_v4_payload()
    attacker = payload["role_adjudication"]["roles"][1]
    attacker.pop("entity_ref")
    attacker["evidence_refs"] = ["E-002"]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="evidence.highlights[0].value",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is True
    roles = {item.role.value: item for item in parsed.result.role_adjudication.roles}
    assert roles["attacker"].value == "30.174.29.44"
    assert roles["attacker"].entity_type == "ip"
    assert any(item.get("operation") == "derive_role_entity_from_unique_cited_value" for item in parsed.hydration_log)


def test_v4_canonicalizes_raw_duplicate_to_runtime_typed_role_entity() -> None:
    payload = _compact_v4_payload()
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.host.host_name",
            value="PBNJ-D0174",
            value_type="string",
            trust_level="high",
            entity_type="host",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="evidence.primary_evidence.content#parsed.computername",
            value="PBNJ-D0174",
            value_type="string",
            trust_level="high",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    attacker = next(role for role in parsed.result.role_adjudication.roles if role.role.value == "attacker")
    assert attacker.entity_type == "host"
    assert attacker.value == "PBNJ-D0174"
    assert "E-A1B2C3D4E5F6" in attacker.evidence_refs
    assert any(item.get("operation") == "canonicalize_role_entity_reference" and item.get("from_evidence_ref") == "E-B1C2D3E4F5A6" and item.get("to_evidence_ref") == "E-A1B2C3D4E5F6" for item in parsed.hydration_log)


def test_v4_unresolved_role_discards_concrete_entity_reference() -> None:
    payload = _compact_v4_payload()
    payload["role_adjudication"]["roles"][1]["status"] = "unresolved"
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    attacker = next(role for role in parsed.result.role_adjudication.roles if role.role.value == "attacker")
    assert attacker.status.value == "unresolved"
    assert attacker.value is None
    assert any(item.get("operation") == "discard_entity_ref_for_unresolved_role" for item in parsed.hydration_log)


def test_v4_materializes_fail_closed_metadata_for_incomplete_role_item() -> None:
    payload = _compact_v4_payload()
    attacker = payload["role_adjudication"]["roles"][1]
    attacker.pop("status")
    attacker.pop("confidence")
    payload["role_adjudication"].pop("status")
    payload["role_adjudication"].pop("rationale")
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    attacker_result = next(role for role in parsed.result.role_adjudication.roles if role.role.value == "attacker")
    assert attacker_result.status.value == "tentative"
    assert attacker_result.confidence == 0.0
    assert parsed.result.role_adjudication.status.value == "tentative"
    assert parsed.repair_applied is True
    operations = {item.get("operation") for item in parsed.hydration_log}
    assert "materialize_conservative_role_status" in operations
    assert "materialize_conservative_role_confidence" in operations
    assert "materialize_role_adjudication_status" in operations
    assert "materialize_role_adjudication_rationale" in operations


def test_v4_drops_unknown_optional_item_field_without_degrading_section() -> None:
    payload = _compact_v4_payload()
    payload["scenario_assessments"][0]["success"] = True
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
            entity_type="ip",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is True
    assert parsed.result.scenario_assessments[0].scenario_name == "反向连接"
    assert any(item.get("operation") == "drop_unsupported_optional_fields" and item.get("fields") == ["success"] for item in parsed.hydration_log)


def test_v4_does_not_guess_missing_role_entity_from_multiple_cited_values() -> None:
    payload = _compact_v4_payload()
    attacker = payload["role_adjudication"]["roles"][1]
    attacker.pop("entity_ref")
    attacker["evidence_refs"] = ["E-001", "E-002"]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    recovery = recover_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert recovery is not None
    assert [item.role.value for item in recovery.result.role_adjudication.roles] == ["victim"]
    assert AnalysisOutputSection.ROLE_ADJUDICATION in recovery.invalid_sections
    assert not any(item.get("operation") == "derive_role_entity_from_unique_cited_value" for item in recovery.hydration_log)


def test_v4_rejects_generic_event_identifier_as_semantic_role_entity() -> None:
    payload = _compact_v4_payload()
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_detection.description",
            value="PIE-2026-69415",
            value_type="string",
            trust_level="high",
        ),
    ]

    recovery = recover_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert recovery is not None
    assert [item.role.value for item in recovery.result.role_adjudication.roles] == ["victim"]
    assert AnalysisOutputSection.ROLE_ADJUDICATION in recovery.invalid_sections
    assert any(item.get("operation") == "reject_untyped_role_entity_reference" for item in recovery.hydration_log)


def test_parse_compact_model_output_v2_materializes_values_and_core_reasoning() -> None:
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(_compact_v2_payload(), ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is False
    assert parsed.model_output_schema_version == "soc.analysis_model_output.v2"
    assert parsed.result.decision_evidence_refs == [
        "E-A1B2C3D4E5F6",
        "E-B1C2D3E4F5A6",
    ]
    assert parsed.result.decision_reasoning_refs == ["R-00"]
    assert parsed.result.reasoning[0].reasoning_id == "R-00"
    assert parsed.result.network_direction.connection_initiator == "30.116.114.150"
    assert {role.role.value: role.value for role in parsed.result.role_adjudication.roles} == {
        "victim": "30.116.114.150",
        "attacker": "30.174.29.44",
    }
    assert parsed.result.role_adjudication.response_target_proposals == []
    assert any(item["operation"] == "materialize_core_decision_reasoning" for item in parsed.hydration_log)


def test_parse_compact_model_output_v3_runtime_owns_reasoning_graph() -> None:
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(_compact_v3_payload(), ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.model_output_schema_version == "soc.analysis_model_output.v3"
    assert [item.reasoning_id for item in parsed.result.reasoning] == [
        "R-00",
        "R-01",
        "R-02",
        "R-03",
        "R-04",
    ]
    assert parsed.result.scenario_assessments[0].reasoning_refs == ["R-01"]
    assert parsed.result.network_direction.reasoning_refs == ["R-02"]
    assert [item.reasoning_refs for item in parsed.result.role_adjudication.roles] == [["R-03"], ["R-04"]]
    assert any(item.get("operation") == "materialize_optional_section_reasoning" for item in parsed.hydration_log)


def test_v3_optional_item_keeps_catalog_refs_and_normalizes_decimal_confidence() -> None:
    payload = _compact_v3_payload()
    scenario = payload["scenario_assessments"][0]
    scenario["confidence"] = "0.9"
    scenario["evidence_refs"] = [
        "E-A1B2C3D4",
        "E-000000000000",
        "E-B1C2D3E4F5A6",
    ]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is True
    assert parsed.result.scenario_assessments[0].confidence == 0.9
    assert parsed.result.scenario_assessments[0].evidence_refs == [
        "E-A1B2C3D4E5F6",
        "E-B1C2D3E4F5A6",
    ]
    normalization = next(item for item in parsed.hydration_log if item.get("operation") == "retain_catalog_backed_optional_evidence_refs")
    assert normalization["removed_refs"] == ["E-000000000000"]
    assert normalization["rewritten_refs"] == [{"from": "E-A1B2C3D4", "to": "E-A1B2C3D4E5F6"}]
    assert any(item.get("operation") == "strict_decimal_string_to_number" for item in parsed.hydration_log)


def test_v3_optional_item_with_no_catalog_evidence_is_dropped_not_guessed() -> None:
    payload = _compact_v3_payload()
    payload["scenario_assessments"][0]["evidence_refs"] = ["E-000000000000"]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    recovery = recover_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert recovery is not None
    assert recovery.repair_applied is True
    assert recovery.result.verdict is Verdict.TRUE_POSITIVE
    assert recovery.result.scenario_assessments == []
    assert AnalysisOutputSection.SCENARIO_ASSESSMENTS in recovery.invalid_sections
    assert any(item.get("operation") == "retain_catalog_backed_optional_evidence_refs" and item.get("retained_count") == 0 for item in recovery.hydration_log)


def test_v3_recovery_keeps_valid_role_when_one_role_has_unknown_entity_ref() -> None:
    payload = _compact_v3_payload()
    payload["role_adjudication"]["roles"][1]["entity_ref"] = "E-000000000000"
    payload["role_adjudication"]["roles"][1]["evidence_refs"] = ["E-000000000000"]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    recovery = recover_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert recovery is not None
    assert recovery.result.verdict is Verdict.TRUE_POSITIVE
    assert [item.role.value for item in recovery.result.role_adjudication.roles] == ["victim"]
    assert recovery.invalid_sections == (AnalysisOutputSection.ROLE_ADJUDICATION,)


def test_v2_recovery_drops_one_bad_optional_reasoning_item_and_keeps_core() -> None:
    payload = _compact_v2_payload()
    payload["reasoning"].append(
        {
            "reasoning_id": "R-02",
            "statement": "This optional inference cites a nonexistent fact.",
            "basis": ["current_evidence"],
            "evidence_refs": ["E-000000000000"],
            "context_refs": [],
            "confidence": 0.5,
        }
    )
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="canonical_entities.network.source_ip",
            value="30.116.114.150",
            value_type="string",
            trust_level="high",
        ),
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-B1C2D3E4F5A6",
            source_path="canonical_entities.network.destination_ip",
            value="30.174.29.44",
            value_type="string",
            trust_level="high",
        ),
    ]

    recovery = recover_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert recovery is not None
    assert recovery.result.verdict is Verdict.TRUE_POSITIVE
    assert [item.reasoning_id for item in recovery.result.reasoning] == [
        "R-00",
        "R-01",
    ]
    assert recovery.invalid_sections == (AnalysisOutputSection.REASONING,)
    issue = next(item for item in recovery.issues if item.error_type == "UnknownReasoningEvidenceReference")
    assert issue.field_paths == ("reasoning.2.evidence_refs",)


def test_parse_compact_model_output_hydrates_runtime_owned_fields() -> None:
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="fact_reconstruction",
            value="svchost.exe",
            value_type="string",
            trust_level="high",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(_compact_payload(), ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is False
    assert parsed.model_output_schema_version == "soc.analysis_model_output.v1"
    assert parsed.result.schema_version == "soc.analysis_result.v4"
    assert parsed.result.evidence[0].model_dump(mode="json") == {
        "evidence_ref": "E-A1B2C3D4E5F6",
        "source": "fact_reconstruction",
        "description": "Runtime-hydrated current-alert catalog fact",
        "value": "svchost.exe",
    }
    assert parsed.result.knowledge_candidates == []
    proposal = parsed.result.role_adjudication.response_target_proposals[0]
    assert proposal.proposal_id == "RT-01"
    assert proposal.policy_review_required is True
    assert proposal.automation_allowed is False
    assert parsed.hydration_log[0]["reference_count"] == 1


def test_parse_compact_model_output_hydrates_redundant_fields_and_drops_empty_unresolved_role() -> None:
    payload = _compact_payload()
    reasoning = payload["reasoning"][0]
    reasoning["reason"] = reasoning.pop("statement")
    reasoning.pop("basis")
    reasoning.pop("context_refs")

    role = payload["role_adjudication"]["roles"][0]
    role["reason"] = role.pop("rationale")
    role.pop("context_refs")
    role["schema_version"] = "model-owned-field-must-be-ignored"
    payload["role_adjudication"]["roles"].append(
        {
            "role": "attacker",
            "entity_type": "ip",
            "value": None,
            "status": "unresolved",
            "confidence": 0.0,
            "evidence_refs": [],
            "reasoning_refs": [],
            "reason": "No attacker entity was present in the bounded evidence.",
        }
    )

    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="fact_reconstruction",
            value="svchost.exe",
            value_type="string",
            trust_level="high",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.result.reasoning[0].statement.startswith("该进程行为")
    assert parsed.result.reasoning[0].basis == [
        "current_evidence",
    ]
    assert parsed.result.reasoning[0].context_refs == []
    assert len(parsed.result.role_adjudication.roles) == 1
    assert parsed.result.role_adjudication.roles[0].rationale == "进程是当前可见的调查对象。"
    operations = {item.get("operation") for item in parsed.hydration_log if item.get("stage") == "runtime_hydration"}
    assert {
        "rename_reason_to_statement",
        "materialize_empty_context_refs",
        "derive_redundant_reasoning_basis",
        "rename_reason_to_rationale",
        "drop_unsupported_unresolved_role",
    } <= operations


def test_parse_compact_model_output_restores_omitted_top_level_version() -> None:
    payload = _compact_payload()
    payload.pop("schema_version")
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="fact_reconstruction",
            value="svchost.exe",
            value_type="string",
            trust_level="high",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is True
    assert parsed.model_output_schema_version == "soc.analysis_model_output.v1"
    assert parsed.result.schema_version == "soc.analysis_result.v4"
    assert parsed.result.evidence[0].value == "svchost.exe"
    assert parsed.repair_log[0] == {
        "stage": "model_output_schema_normalization",
        "repair": "restore_unambiguous_compact_schema_version",
        "schema_version": "soc.analysis_model_output.v1",
    }


def test_missing_version_is_not_inferred_for_legacy_runtime_owned_shape() -> None:
    payload = _valid_payload()
    payload.pop("schema_version")

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"
    assert "schema_version" in exc.value.field_paths


def test_compact_output_discards_model_supplied_runtime_owned_fields() -> None:
    payload = _compact_payload()
    payload["evidence"] = [{"invented": "must-not-enter-runtime"}]
    payload["knowledge_candidates"] = [{"invented": "must-not-enter-runtime"}]
    catalog = [
        AnalysisEvidenceCatalogItem(
            evidence_ref="E-A1B2C3D4E5F6",
            source_path="fact_reconstruction",
            value="svchost.exe",
            value_type="string",
            trust_level="high",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        evidence_catalog=catalog,
    )

    assert parsed.repair_applied is False
    assert parsed.result.evidence[0].value == "svchost.exe"
    assert parsed.result.knowledge_candidates == []
    assert parsed.hydration_log[-1] == {
        "stage": "runtime_hydration",
        "operation": "discard_model_supplied_runtime_owned_fields",
        "fields": ["evidence", "knowledge_candidates"],
    }


def test_parse_analysis_result_accepts_strict_json() -> None:
    parsed = parse_analysis_result_output(json.dumps(_valid_payload(), ensure_ascii=False))

    assert parsed.parser_version == ANALYSIS_JSON_PARSER_VERSION
    assert parsed.repair_applied is False
    assert parsed.result.verdict == Verdict.SUSPICIOUS
    assert parsed.result.confidence == 0.76


def test_parse_analysis_result_accepts_unresolved_role_without_entity_value() -> None:
    payload = _valid_payload()
    payload["role_adjudication"]["roles"] = [
        {
            "role": "attacker",
            "entity_type": "ip",
            "value": None,
            "status": "unresolved",
            "confidence": 0.35,
            "evidence_refs": ["E-A1B2C3D4E5F6"],
            "reasoning_refs": ["R-01"],
            "context_refs": [],
            "rationale": "当前证据无法把攻击者角色绑定到具体 IP。",
        }
    ]
    payload["role_adjudication"]["response_target_proposals"] = []

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    role = parsed.result.role_adjudication.roles[0]
    assert role.status.value == "unresolved"
    assert role.value is None


def test_parse_analysis_result_rejects_unresolved_role_with_concrete_value() -> None:
    payload = _valid_payload()
    payload["role_adjudication"]["roles"][0]["status"] = "unresolved"

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "schema_validation"


def test_recover_analysis_result_keeps_core_and_rejects_only_invalid_role_section() -> None:
    payload = _valid_payload()
    payload["role_adjudication"]["roles"][0]["status"] = "unresolved"

    recovery = recover_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert recovery is not None
    assert [section.value for section in recovery.invalid_sections] == ["role_adjudication"]
    assert recovery.result.verdict == Verdict.SUSPICIOUS
    assert recovery.result.scenario_assessments
    assert recovery.result.network_direction.status.value == "indeterminate"
    assert recovery.result.role_adjudication.status.value == "not_assessed"


def test_parse_analysis_section_patch_merges_only_rejected_section() -> None:
    payload = _valid_payload()
    payload["role_adjudication"]["roles"][0]["status"] = "unresolved"
    recovery = recover_analysis_result_output(json.dumps(payload, ensure_ascii=False))
    assert recovery is not None
    corrected_role = dict(payload["role_adjudication"])
    corrected_role["roles"] = [dict(payload["role_adjudication"]["roles"][0])]
    corrected_role["roles"][0]["value"] = None
    corrected_role["response_target_proposals"] = []

    parsed = parse_analysis_section_patch_output(
        json.dumps(
            {
                "schema_version": "soc.analysis_section_patch.v1",
                "sections": {"role_adjudication": corrected_role},
            },
            ensure_ascii=False,
        ),
        recovery=recovery,
    )

    assert parsed.result.summary == payload["summary"]
    assert parsed.result.role_adjudication.roles[0].value is None


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

    context_catalog = [
        AnalysisContextCatalogItem(
            context_ref="C-ABCDEF123456",
            kind="governed_context",
            label="reviewed network boundary",
            source_id="context/network-boundary",
            summary="Reviewed organization-boundary context.",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        context_catalog=context_catalog,
    )

    assert parsed.result.network_direction.semantic_direction == "victim_to_attacker_reverse_connection"
    assert parsed.result.role_adjudication.roles[0].role.value == "victim"
    target = parsed.result.role_adjudication.response_target_proposals[0]
    assert target.action_kind == "isolate_host"
    assert target.target_value == "30.116.114.150"
    assert target.automation_allowed is False


def test_parser_rejects_free_form_conflict_when_model_roles_match_coherent_reverse_mapping() -> None:
    payload = _valid_payload()
    payload["role_adjudication"] = {
        "schema_version": "soc.role_adjudication_result.v1",
        "status": "tentative",
        "roles": [
            {
                "role": "attacker",
                "entity_type": "ip",
                "value": "30.174.29.44",
                "status": "tentative",
                "confidence": 0.6,
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "reasoning_refs": ["R-01"],
                "context_refs": [],
                "rationale": "响应方是攻击端候选。",
            },
            {
                "role": "victim",
                "entity_type": "ip",
                "value": "30.116.114.150",
                "status": "tentative",
                "confidence": 0.6,
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "reasoning_refs": ["R-01"],
                "context_refs": [],
                "rationale": "发起回连的一方是受害端候选。",
            },
        ],
        "response_target_proposals": [],
        "conflicts": ["攻击者和受害者角色与反弹连接方向冲突。"],
        "evidence_gaps": ["缺少端点证据。"],
        "rationale": "角色值与反弹连接关系一致。",
    }
    request = LLMAnalysisRequest(
        alert_id="reverse-shell",
        fact_reconstruction=FactReconstructionResult(
            role_coherence=RoleCoherenceAssessment(
                scenario_type="reverse_connection",
                status=RoleCoherenceStatus.COHERENT,
                relationships=[
                    RoleCoherenceRelationship(
                        semantic_role="attacker",
                        network_role="destination",
                        semantic_value="30.174.29.44",
                        network_value="30.174.29.44",
                        status=RoleCoherenceRelationshipStatus.ALIGNED,
                    ),
                    RoleCoherenceRelationship(
                        semantic_role="victim",
                        network_role="source",
                        semantic_value="30.116.114.150",
                        network_value="30.116.114.150",
                        status=RoleCoherenceRelationshipStatus.ALIGNED,
                    ),
                ],
                rationale="Reverse-connection roles align.",
            )
        ),
    )

    with pytest.raises(LLMOutputParseError) as exc_info:
        parse_analysis_result_output(
            json.dumps(payload, ensure_ascii=False),
            analysis_request=request,
        )

    assert exc_info.value.stage == "role_coherence_validation"
    assert exc_info.value.issue_codes == ("unsupported_role_conflict",)

    recovery = recover_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        analysis_request=request,
    )
    assert recovery is not None
    assert [section.value for section in recovery.invalid_sections] == ["role_adjudication"]


def test_parse_analysis_result_accepts_direct_direction_context_not_repeated_in_reasoning() -> None:
    payload = _valid_payload()
    payload["network_direction"]["context_refs"] = ["C-ABCDEF123456"]
    context_catalog = [
        AnalysisContextCatalogItem(
            context_ref="C-ABCDEF123456",
            kind="governed_context",
            label="reviewed network boundary",
            source_id="context/network-boundary",
            summary="Reviewed organization-boundary context.",
        )
    ]

    parsed = parse_analysis_result_output(
        json.dumps(payload, ensure_ascii=False),
        context_catalog=context_catalog,
    )

    assert parsed.result.reasoning[0].context_refs == []
    assert parsed.result.network_direction.context_refs == ["C-ABCDEF123456"]


def test_parse_analysis_result_rejects_unknown_direct_direction_context() -> None:
    payload = _valid_payload()
    payload["network_direction"]["context_refs"] = ["C-ABCDEF123456"]

    with pytest.raises(LLMOutputParseError) as exc:
        parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert exc.value.stage == "reference_validation"
    assert "C-ABCDEF123456" in str(exc.value)


def test_parse_analysis_result_keeps_target_role_that_differs_for_same_entity() -> None:
    payload = _valid_payload()
    payload["role_adjudication"]["roles"][0]["role"] = "victim"

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    targets = parsed.result.role_adjudication.response_target_proposals
    assert len(targets) == 1
    assert parsed.result.role_adjudication.roles[0].role.value == "victim"
    assert targets[0].target_role.value == "impacted_asset"
    assert parsed.repair_applied is False


def test_parse_analysis_result_drops_target_without_adjudicated_entity() -> None:
    payload = _valid_payload()
    payload["role_adjudication"]["response_target_proposals"][0].update(
        {
            "target_role": "attacker",
            "target_value": "198.51.100.44",
        }
    )

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.result.role_adjudication.response_target_proposals == []
    repair = next(item for item in parsed.repair_log if item["repair"] == "remove_response_targets_without_adjudicated_entities")
    assert repair["removed"] == [
        {
            "index": 0,
            "proposal_id": "RT-01",
            "target_role": "attacker",
            "target_type": "process",
            "target_value": "198.51.100.44",
            "reason": "target_entity_not_adjudicated",
        }
    ]


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


def test_parse_analysis_result_accepts_supported_core_without_optional_scenario_or_gap() -> None:
    payload = _valid_payload()
    payload["scenario_assessments"] = []
    payload["evidence_gaps"] = []

    parsed = parse_analysis_result_output(json.dumps(payload, ensure_ascii=False))

    assert parsed.result.verdict is Verdict.SUSPICIOUS
    assert parsed.result.scenario_assessments == []
    assert parsed.result.evidence_gaps == []


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
