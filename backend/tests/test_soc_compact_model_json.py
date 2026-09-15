from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from soc_agent.contracts import NormalizationAssistRequest
from soc_agent.llm.memory_lesson import _build_output_repair_messages
from soc_agent.prompts import analysis, memory_lesson, normalization, output_repair, role_verification, tenant_policy

CONTEXT = {
    "prompt_example_id": "non_network",
    "facts": [{"path": "C:/Program Files/app.exe", "command": "a  b\t--flag\nnext", "quote": 'cmd="hello world"', "label": "中文事实", "active": False, "count": 0, "missing": None}],
}


def _json_blocks(text: str):
    decoder = json.JSONDecoder()
    for match in re.finditer(r"^[\[{]", text, re.MULTILINE):
        try:
            value, end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        yield text[match.start() : match.start() + end], value


@pytest.mark.parametrize("stage", ["analysis", "memory_lesson", "role_verification", "output_repair", "memory_repair", "normalization", "tenant_policy"])
def test_every_soc_model_json_block_is_compact_and_lossless(stage, monkeypatch):
    schema = {"result": "中文结果", "details": [{"value": "retain  spaces"}]}
    if stage == "analysis":
        texts = [analysis._user_prompt(CONTEXT, response_schema=schema)]
    elif stage == "memory_lesson":
        texts = [memory_lesson._user_prompt(CONTEXT, response_schema=schema)]
    elif stage == "role_verification":
        texts = [role_verification._system_prompt(schema), role_verification._user_prompt(CONTEXT, response_schema=schema)]
    elif stage == "output_repair":
        texts = [output_repair._build_prompt(prompt_version="test", object_name="test", context=CONTEXT, additional_rules=[]).user]
    elif stage == "memory_repair":
        texts = [m["content"] for m in _build_output_repair_messages(prompt_context=CONTEXT, response_schema=schema, invalid_output="bad  JSON\noriginal", validation_error="shape")]
    elif stage == "normalization":
        request = NormalizationAssistRequest(alert_id="compact-test", source={}, detection={}, configuration_hash="test", source_text="C:/Program Files/app.exe  --flag\noriginal")
        texts = [m["content"] for m in normalization.build_normalization_prompt(request)]
    else:
        monkeypatch.setattr(tenant_policy, "project_analysis_context", lambda _: CONTEXT)
        policy = SimpleNamespace(policy_id="test", policy_version="v1", tenant_id="test", policy_mode=SimpleNamespace(value="shadow"), owner="operator", source_ref="test")
        record = SimpleNamespace(model_dump=lambda **_: CONTEXT)
        run = SimpleNamespace(llm_analysis_request=object(), analysis=record, decision=record)
        prompt = tenant_policy.build_tenant_policy_advisor_prompt(policy, run, skill_content="retain  spaces\nnext", skill_name="test", skill_version="v1")
        texts = [m["content"] for m in prompt.messages()]
    blocks = [block for text in texts for block in _json_blocks(text)]
    assert blocks, stage
    for serialized, value in blocks:
        assert serialized == json.dumps(value, ensure_ascii=False, separators=(",", ":")), stage
    if stage not in {"normalization", "tenant_policy", "memory_repair"}:
        assert any(value == CONTEXT for _, value in blocks)
    else:
        assert "C:/Program Files/app.exe" in "\n".join(texts)
    if stage == "normalization":
        assert json.loads(texts[1])["sources"][0]["text"] == request.source_text
    if stage == "memory_repair":
        assert json.loads(texts[1])["invalid_output"] == "bad  JSON\noriginal"


def test_compact_serializer_preserves_strings_order_nulls_and_numeric_values():
    from soc_agent.utils.model_json import model_json

    original = json.loads(json.dumps(CONTEXT))
    rendered = model_json(CONTEXT)
    assert json.loads(rendered) == original
    assert CONTEXT == original
    assert rendered == json.dumps(CONTEXT, ensure_ascii=False, separators=(",", ":"))
    assert "中文事实" in rendered
    assert json.loads(rendered)["facts"][0]["command"] == "a  b\t--flag\nnext"
    assert model_json({"z": 1, "a": 2}, sort_keys=True) == '{"a":2,"z":1}'
    with pytest.raises(TypeError):
        model_json({"unsupported": object()})


def test_prompt_modules_do_not_reintroduce_expanded_json_serialization():
    root = Path(analysis.__file__).parent
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or not isinstance(node.func.value, ast.Name):
                continue
            if (node.func.value.id, node.func.attr) != ("json", "dumps"):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}
            assert "indent" not in keywords, (path.name, node.lineno)
            assert "separators" in keywords, (path.name, node.lineno)
            assert ast.literal_eval(keywords["separators"]) == (",", ":")


def test_normalization_serializes_structured_source_without_touching_string_values():
    from soc_agent.contracts import AlertInput
    from soc_agent.llm.normalization import JsonLLMNormalizationReviewer

    raw = {"message": CONTEXT}
    alert = AlertInput(alert_id="compact-source", raw=raw, extensions={"evidence_input_policy": {"name": "raw_message_first", "selected_input_path": "message", "selected_layer": "raw_message", "trust_level": "high"}})
    reviewer = JsonLLMNormalizationReviewer(client=SimpleNamespace(), model_name="test")
    request = reviewer.prepare(alert)
    assert json.loads(request.source_text) == CONTEXT
    assert request.source_text == json.dumps(CONTEXT, ensure_ascii=False, separators=(",", ":"))
    assert raw == {"message": CONTEXT}


def test_asset_tool_model_text_is_compact_and_matches_structured_result():
    from soc_agent.integrations.pingan.asset_mcp_server import _handle_message

    record = SimpleNamespace(model_dump=lambda **_: CONTEXT)
    locator = SimpleNamespace(locate=lambda _: record)
    response = _handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "asset_locate", "arguments": {"query": "198.51.100.1"}}}, locator=locator, startup_error=None)
    result = response["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == json.dumps(result["structuredContent"], ensure_ascii=False, separators=(",", ":"))
