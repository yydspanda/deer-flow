from __future__ import annotations

import pytest

from soc_agent.contracts import AlertClassification, AlertSourceRef, AnalysisContextCatalogItem, AnalysisMemoryContextComparison, AnalysisRun, DetectionRuleRef, LLMAnalysisRequest
from soc_agent.memory.matching_facts import memory_matching_facts


def _run(**comparison_fields):
    comparison = AnalysisMemoryContextComparison(use_mode="context_only", **comparison_fields)
    request = LLMAnalysisRequest(
        alert_id="ALERT-SCOPE",
        source=AlertSourceRef(source_type="ndr", source_system="vendor-a", product="network-sensor"),
        detection=DetectionRuleRef(detection_key="network:rule:17"),
        classification=AlertClassification(),
        context_catalog=[AnalysisContextCatalogItem(context_ref="M-000000000001", kind="confirmed_memory", source_id="MEM-ONE@v2", label="Reviewed experience", summary="Only endpoint values differ.", memory_comparison=comparison)],
    )
    return AnalysisRun(run_id="RUN-SCOPE", alert_id=request.alert_id, status="success", llm_analysis_request=request)


def test_matching_facts_use_frozen_comparison_not_model_or_memory_prose():
    run = _run(uncovered_behavior_components=["detected_behavior:network:vendor-a:network-sensor:id:0x42@service:sip/5060"])
    before = run.model_dump_json()
    facts = memory_matching_facts(run)
    assert len(facts) == 1
    assert "MEM-ONE@v2" in facts[0]
    assert "仅供模型参考" in facts[0]
    assert "当前新增、旧经验未覆盖" in facts[0]
    assert "0x42" in facts[0] and "SIP/5060" in facts[0]
    assert "Only endpoint" not in facts[0]
    assert "不是风险结论" in facts[0]
    assert run.model_dump_json() == before


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"missing_behavior_components": ["network_service:tcp/443"]}, "旧经验要求、当前未满足"),
        ({"missing_required_facet_keys": ["environment"]}, "数据使用范围"),
        ({"missing_reuse_conditions": [{"facet_key": "role_entity", "value_prefix": "destination", "values": ["destination:192.0.2.10"]}]}, "直接复用的附加限制未满足"),
        ({"excluded_facet_hits": {"entity": ["host:test"]}}, "命中排除条件"),
        ({}, "未记录明确条件差异"),
    ],
)
def test_matching_facts_distinguish_different_scope_reasons(fields, expected):
    assert expected in " ".join(memory_matching_facts(_run(**fields)))


def test_absent_comparison_is_not_an_exact_match_or_fabricated_delta():
    run = _run()
    run.llm_analysis_request.context_catalog[0].memory_comparison = None
    assert "未保存匹配比较" in memory_matching_facts(run)[0]
    run.llm_analysis_request.context_catalog = []
    assert memory_matching_facts(run) == []


def test_eligible_directive_does_not_claim_it_was_applied():
    run = _run()
    run.llm_analysis_request.context_catalog[0].memory_comparison = AnalysisMemoryContextComparison(use_mode="directive_applicable", decision_directive_applicable=True, applicability_status="applicable")
    facts = " ".join(memory_matching_facts(run))
    assert "具备直接复用资格" in facts
    assert "不等于已经执行复用" in facts


def test_exact_context_does_not_claim_directive_authority():
    run = _run()
    run.llm_analysis_request.context_catalog[0].memory_comparison = AnalysisMemoryContextComparison(use_mode="exact_context", applicability_status="applicable")
    facts = " ".join(memory_matching_facts(run))
    assert "审核条件匹配" in facts
    assert "不承载直接复用指令" in facts


def test_many_memories_have_an_explicit_display_bound():
    run = _run()
    template = run.llm_analysis_request.context_catalog[0]
    run.llm_analysis_request.context_catalog = [template.model_copy(update={"context_ref": f"M-{i:012d}", "source_id": f"MEM-{i}@v2"}) for i in range(25)]
    facts = memory_matching_facts(run)
    assert len(facts) == 20
    assert "另有 6 条经验" in facts[-1]


def test_matching_facts_bound_many_deltas_without_claiming_complete_display():
    run = _run(uncovered_behavior_components=[f"network_service:tcp/{port}" for port in range(1, 101)])
    facts = memory_matching_facts(run)
    assert "另有" in facts[0]
    assert "完整匹配比较" in facts[0]
    assert len(facts[0]) < 2000
