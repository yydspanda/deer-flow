"""Synthetic safety checks for the isolated extraction experiment."""

import copy
import json

import pytest

from validation.compact_zeus.audits.normalization_assistance_trial import (
    SYSTEM_PROMPT,
    build_comparison,
    build_messages,
    build_model_overrides,
    check_quoted_kv,
    decode_extraction_response,
    merge_facts,
    replay_saved_attempt,
    select_source,
)
from soc_agent.normalizers import normalize_alert_payload


def payload(message):
    return {
        "tenant_id": "pingan",
        "alert": {
            "id": "trial-synthetic",
            "ruleCode": "TRIAL_RULE",
            "hitLog": [{"zeusRawLogs": [{"message": message}]}],
        },
    }


def fact(target, value, quote, **extra):
    return {
        "target": target,
        "value": value,
        "source_ref": "X-1",
        "source_quote": quote,
        **extra,
    }


def test_current_parser_residual_is_visible_without_repair():
    message = 'hostname="host-a" cmdline=""C:\\Tools\\tool.exe" --read" kind="event"'
    check = check_quoted_kv(message)
    assert check["empty_fields"] == ["cmdline"]
    assert check["residuals"][0]["text"] == 'C:\\Tools\\tool.exe" --read"'
    assert (
        message[check["residuals"][0]["start"] : check["residuals"][0]["end"]]
        == check["residuals"][0]["text"]
    )


def test_single_source_only_and_no_outer_fallback():
    data = payload('hostname="host-a" kind="event"')
    data["alert"]["hostname"] = "outer-host"
    data["relatedAlertList"] = [{"hostname": "unrelated-host"}]
    source = select_source(data)
    assert "outer-host" not in source["text"]
    data["alert"]["hitLog"][0]["zeusRawLogs"].append({"message": 'hostname="host-b"'})
    with pytest.raises(ValueError, match="exactly one"):
        select_source(data)


def test_duplicate_keys_are_not_disguised_as_complete():
    assert check_quoted_kv('host="a" host="b"')["duplicate_keys"] == ["host"]


@pytest.mark.parametrize("budget", [2048, 4096, 8192])
def test_generation_budget_changes_no_other_trial_controls(budget):
    overrides = build_model_overrides(budget)
    assert overrides.pop("max_tokens") == budget
    assert overrides == {
        "max_retries": 0,
        "timeout": 90,
        "temperature": 0,
        "disable_streaming": True,
    }


@pytest.mark.parametrize("budget", [0, -1])
def test_invalid_generation_budget_is_rejected_before_provider_call(budget):
    with pytest.raises(ValueError, match="positive"):
        build_model_overrides(budget)


@pytest.mark.parametrize("content", ['{"facts":[],"unresolved":[]}', "unfinished"])
def test_truncated_provider_output_is_not_a_json_contract_error(content):
    with pytest.raises(ValueError, match="^provider_output_truncated$"):
        decode_extraction_response(content, {"finish_reason": "length"})


def test_complete_extraction_response_still_accepts_json_fences():
    result = decode_extraction_response(
        '```json\n{"facts":[],"unresolved":[]}\n```', {"finish_reason": "stop"}
    )
    assert result.facts == []


@pytest.mark.parametrize("wire_content", ["", '{"facts":[]}', "SYNTHETIC REASONING"])
def test_sdk_sends_disable_flag_and_does_not_promote_reasoning(wire_content):
    import httpx
    from deerflow.config.app_config import AppConfig
    from deerflow.models.factory import create_chat_model

    config = AppConfig.model_validate(
        {
            "models": [
                {
                    "name": "synthetic-deepseek",
                    "model": "deepseek-v4-flash-0731",
                    "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
                    "api_key": "synthetic-not-a-secret",
                    "api_base": "https://synthetic.invalid/v1",
                    "when_thinking_disabled": {
                        "extra_body": {"thinking": {"type": "disabled"}}
                    },
                }
            ],
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
        }
    )
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": wire_content,
                            "reasoning_content": "SYNTHETIC REASONING",
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        model = create_chat_model(
            name="synthetic-deepseek",
            thinking_enabled=False,
            app_config=config,
            attach_tracing=False,
            model_overrides={
                "http_client": http_client,
                "disable_streaming": True,
                "max_tokens": 256,
                "max_retries": 0,
            },
        )
        result = model.invoke([{"role": "user", "content": "synthetic request"}])
    assert len(requests) == 1
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["stream"] is False
    assert requests[0]["model"] == "deepseek-v4-flash-0731"
    assert "extra_body" not in requests[0]
    assert result.content == wire_content
    assert result.additional_kwargs["reasoning_content"] == "SYNTHETIC REASONING"


def test_prompt_example_is_json_and_raw_text_is_not_double_encoded():
    example = SYSTEM_PROMPT.split("不是当前日志事实：\n", 1)[1]
    assert json.JSONDecoder().raw_decode(example)[0]["facts"]
    data = payload('hostname="a" proc_path="C:\\Tools\\x.exe"')
    source = select_source(data)
    messages = build_messages(source, normalize_alert_payload(data))
    assert (
        "BEGIN RAW SOURCE\n" + source["text"] + "\nEND RAW SOURCE"
        in messages[1]["content"]
    )


def test_empty_supplement_does_not_change_existing_entities():
    data = payload('hostname="a" kind="event"')
    before = normalize_alert_payload(data)
    before.entities.process.process_path = r"C:\Tools\existing.exe"
    after, _ = merge_facts(before, select_source(data), [])
    assert after.entities == before.entities


def test_failed_saved_response_preserves_existing_facts_without_a_model(tmp_path):
    data = payload('hostname="a" kind="event"')
    before = normalize_alert_payload(data)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    for name, value in {
        "01-source.json": {"source": select_source(data)},
        "02-adapter-before.json": before.model_dump(mode="json"),
        "05-llm-response.json": {
            "content": "not a final response",
            "metadata": {"finish_reason": "length"},
            "usage": {},
        },
        "00-experiment.json": {"synthetic": True},
    }.items():
        (attempt / name).write_text(json.dumps(value), encoding="utf-8")
    result = replay_saved_attempt(attempt, tmp_path / "review")
    assert result["before"] == result["after"]
    assert result["assistance_outcome"] == "not_applied"
    assert result["new_llm_calls"] == 0
    assert result["memory_or_candidate_written"] is False


def test_valid_facts_enter_existing_consumers_without_mutating_input():
    message = 'hostname="host-a" proc_path="C:\\Tools\\tool.exe" file_path="D:\\Scan\\artifact.exe" client_ip="10.0.0.8"'
    data = payload(message)
    before = normalize_alert_payload(data)
    frozen = copy.deepcopy(before.model_dump(mode="json"))
    after, result = merge_facts(
        before,
        select_source(data),
        [
            fact(
                "entities.process.process_path",
                r"C:\Tools\tool.exe",
                'proc_path="C:\\Tools\\tool.exe"',
            ),
            fact(
                "entities.file.file_path",
                r"D:\Scan\artifact.exe",
                'file_path="D:\\Scan\\artifact.exe"',
            ),
            fact("entities.host.ip_addresses", "10.0.0.8", 'client_ip="10.0.0.8"'),
        ],
    )
    assert before.model_dump(mode="json") == frozen
    assert after.raw == before.raw
    assert len(result["accepted"]) == 3
    assert after.entities.network.source_ip is None
    assert after.entities.file.observations[0].process_id is None
    assert after.entities.file.observations[0].relation.value == "observed_artifact"
    comparison, _ = build_comparison(before, after)
    assert "process_image:tool.exe" in comparison["after"]["behavior_component_core"]
    assert (
        comparison["before"]["behavior_fingerprint"]
        != comparison["after"]["behavior_fingerprint"]
    )


@pytest.mark.parametrize(
    "proposal",
    [
        fact("entities.host.host_name", "invented", 'hostname="host-a"'),
        fact(
            "entities.host.host_name", "host-a", 'hostname="host-a"', source_ref="X-2"
        ),
        fact("entities.network.source_ip", "10.0.0.8", 'client_ip="10.0.0.8"'),
        fact("entities.host.ip_addresses", "invalid-ip", 'client_ip="invalid-ip"'),
    ],
)
def test_invalid_facts_are_isolated(proposal):
    data = payload('hostname="host-a" client_ip="10.0.0.8" other="invalid-ip"')
    before = normalize_alert_payload(data)
    after, result = merge_facts(before, select_source(data), [proposal])
    assert not result["accepted"]
    assert len(result["rejected"]) == 1
    assert after.entities == before.entities


def test_conflicts_do_not_silently_overwrite_or_pick_first():
    data = payload('hostname="host-a" other="host-b" kind="event"')
    before = normalize_alert_payload(data)
    before.entities.host.host_name = "known-host"
    after, result = merge_facts(
        before,
        select_source(data),
        [
            fact("entities.host.host_name", "host-a", 'hostname="host-a"'),
        ],
    )
    assert after.entities.host.host_name == "known-host"
    assert result["rejected"][0]["reason"] == "existing_value_conflict"
    before.entities.host.host_name = None
    after, result = merge_facts(
        before,
        select_source(data),
        [
            fact("entities.host.host_name", "host-a", 'hostname="host-a"'),
            fact("entities.host.host_name", "host-b", 'other="host-b"'),
        ],
    )
    assert after.entities.host.host_name is None
    assert len(result["rejected"]) == 2


def test_ambiguous_quote_and_unbounded_source_rejected():
    data = payload('a="host-a" b="host-a"')
    _, result = merge_facts(
        normalize_alert_payload(data),
        select_source(data),
        [
            fact("entities.host.host_name", "host-a", "host-a"),
        ],
    )
    assert result["rejected"][0]["reason"] == "ambiguous_or_missing_quote"
    with pytest.raises(ValueError, match="budget"):
        select_source(payload("x" * 12001))
