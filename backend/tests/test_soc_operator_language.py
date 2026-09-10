from __future__ import annotations

import json
import re

import pytest

from soc_agent.contracts import TenantPolicyAdvice
from soc_agent.core.operator_language import operator_text
from soc_agent.llm.memory_lesson import _build_output_repair_messages as lesson_repair
from soc_agent.prompts.analysis import _system_prompt as analysis_system
from soc_agent.prompts.memory_lesson import _system_prompt as lesson_system
from soc_agent.prompts.operator_language import OPERATOR_OUTPUT_LANGUAGE
from soc_agent.prompts.output_repair import _build_prompt as repair_prompt
from soc_agent.prompts.role_verification import _role_verification_response_schema
from soc_agent.prompts.role_verification import _system_prompt as role_system
from soc_agent.prompts.tenant_policy import _response_schema, policy_output_examples


def test_all_analysis_and_repair_nodes_request_chinese_operator_prose() -> None:
    repair = repair_prompt(prompt_version="test", object_name="test", context={}, additional_rules=[])
    for system in (role_system(_role_verification_response_schema()), repair.system):
        assert OPERATOR_OUTPUT_LANGUAGE in system
    assert "concise analyst-facing Chinese" in analysis_system()
    assert "Write concise analyst-facing Chinese" in lesson_system()
    assert OPERATOR_OUTPUT_LANGUAGE in lesson_repair(prompt_context={}, response_schema={}, invalid_output="{}", validation_error="test")[0]["content"]
    assert "JSON keys" in OPERATOR_OUTPUT_LANGUAGE
    assert "raw evidence" in OPERATOR_OUTPUT_LANGUAGE
    assert "suggested_action" in OPERATOR_OUTPUT_LANGUAGE
    assert "counterevidence_assessment" in OPERATOR_OUTPUT_LANGUAGE


def test_policy_prose_shape_and_valid_examples_are_chinese() -> None:
    shape = _response_schema()
    for field in ("summary", "suggested_action", "rationale", "manual_checks"):
        assert re.search(r"[\u4e00-\u9fff]", str(shape[field])), field
    examples = [TenantPolicyAdvice.model_validate(item) for item in policy_output_examples()]
    assert {item.evaluation_status.value for item in examples} == {"matched", "no_match"}
    for example in examples:
        for text in [example.summary, example.suggested_action, *example.rationale, *example.manual_checks]:
            if text:
                assert re.search(r"[\u4e00-\u9fff]", text)
        assert all(ref.startswith("EX-") for ref in example.evidence_refs)
    assert json.dumps(policy_output_examples(), ensure_ascii=False)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("No eligible reviewed Memory decision directive was present.", "没有可直接复用结论的已审核经验；模型仍可能使用参考经验。"),
        ("Tenant policy application is disabled by operator configuration.", "企业策略未启用，保留前序研判结果。"),
        ("Final governed decision after Memory, tenant policy, and optional automation policy evaluation.", "综合已审核经验、企业策略及已配置的自动化策略，形成最终采用结果；不代表外部处置已执行。"),
    ],
)
def test_fixed_stage_explanations_are_localized_without_changing_records(source: str, expected: str) -> None:
    record = {"summary": source}
    assert operator_text(record["summary"]) == expected
    assert record["summary"] == source


def test_operator_copy_does_not_invent_translation_for_model_prose_or_identifiers() -> None:
    for value in ("Investigate POST /web/sys/user; SQL injection remains possible.", "E-001", "RPAADM_000558", "检查 POST /web/sys/user。"):
        assert operator_text(value) == value


def test_blocker_summary_preserves_reason_codes() -> None:
    source = "Decision-level review prevents adopting a handling plan or automatic action: role_conflict, invalid_reference. Original verdict and policy advice remain in prior stages."
    result = operator_text(source)
    assert "role_conflict, invalid_reference" in result
    assert "人工确认" in result
    assert "Decision-level" not in result
