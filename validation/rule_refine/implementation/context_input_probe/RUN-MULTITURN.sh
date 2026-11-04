bash <<'SOC_MULTITURN_CONTEXT'
set -euo pipefail
umask 077
probe_project="$HOME/sec_know_model"
probe_temp="$(mktemp -d "${TMPDIR:-/tmp}/sec-know-multiturn.XXXXXX")"
trap 'rm -f "$probe_temp/probe.py" "$probe_temp/probe_multiturn.py"; rmdir "$probe_temp"' EXIT
cat > "$probe_temp/probe.py" <<'SOC_MULTITURN_BASELINE_PYTHON'
"""One synthetic long-input POST to the configured upstream; no application imports."""

import argparse
import ast
import hashlib
import json
import os
import random
import re
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ALIAS = "Pub_DeepSeek_V4_Flash"
THRESHOLD = 262144
DEFAULT_UNITS = 320000
SEED = 20261104


class ProbeError(ValueError):
    """Fixed diagnostic codes only, never config or provider text."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dictionary(node):
    if not isinstance(node, ast.Dict):
        raise ProbeError("literal_config_dictionary_required")
    result = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ProbeError("literal_config_keys_required")
        if key.value in result:
            raise ProbeError("duplicate_config_key")
        result[key.value] = value
    return result


def load_route(root):
    source = (root / "llm_service/openai_completion.py").read_bytes()
    nodes = []
    for node in ast.parse(source.decode("utf-8-sig")).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(t, ast.Name) and t.id == "PRD_MODEL_CONFIGS" for t in targets
            ):
                nodes.append(node.value)
    if len(nodes) != 1:
        raise ProbeError("unique_PRD_MODEL_CONFIGS_required")
    route_node = dictionary(nodes[0]).get(ALIAS)
    fields = dictionary(route_node)
    route = {}
    for name in ("type", "base_url", "upstream_model", "api_key"):
        node = fields.get(name)
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, str)
            or not node.value
        ):
            raise ProbeError("literal_OPENAI_route_required")
        route[name] = node.value
    url = urlsplit(route["base_url"])
    if (
        route["type"] != "OPENAI"
        or route["upstream_model"] != "deepseek-v4-flash"
        or url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or any(c.isspace() or ord(c) < 32 for c in route["base_url"])
        or not all(32 < ord(c) < 127 for c in route["api_key"])
    ):
        raise ProbeError("invalid_selected_OPENAI_route")
    route["source_sha256"] = hashlib.sha256(source).hexdigest()
    return route


def make_request(model, units):
    if units < 1:
        raise ProbeError("positive_synthetic_units_required")
    rng = random.Random(SEED)
    filler = " ".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=units))
    return {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": "This is a context-length test. Reply only OK.",
            },
            {
                "role": "user",
                "content": "Synthetic filler begins:\n"
                + filler
                + "\nEnd of filler. Reply only OK.",
            },
        ],
    }


def post_once(url, key, body, timeout):
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        return response.code, response.read().decode("utf-8", errors="replace")


def redact(text, secret):
    text = text.replace(secret, "[credential omitted]")
    text = re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [omitted]", text)
    text = re.sub(
        r"(?i)([\"']?(?:api_key|app_secret|password|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+",
        r"\1[omitted]",
        text,
    )
    return text


def integer(value):
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def assess(http_status, body):
    try:
        data = json.loads(body)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    choices = data.get("choices")
    choice = (
        choices[0]
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else {}
    )
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    prompt_tokens = integer(usage.get("prompt_tokens"))
    result_code = data.get("resultCode", "0")
    success = (
        200 <= http_status < 300
        and not data.get("error")
        and not isinstance(result_code, bool)
        and result_code in ("0", 0)
        and data.get("success") is not False
        and isinstance(message.get("content"), str)
        and bool(message["content"].strip())
        and choice.get("finish_reason") in {"stop", "length"}
    )
    result = {
        "http_status": http_status,
        "valid_completion_received": success,
        "returned_model": data.get("model")
        if isinstance(data.get("model"), str)
        else None,
        "finish_reason": choice.get("finish_reason"),
        "usage": {
            name: integer(usage.get(name))
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "input_over_256k_verified": False,
        "accepted_over_256k": None,
        "verdict": "request_failed_capacity_unknown",
    }
    if success:
        over = prompt_tokens is not None and prompt_tokens > THRESHOLD
        result.update(
            input_over_256k_verified=over,
            accepted_over_256k=True if over else None,
            verdict="accepted_over_256k"
            if over
            else "completed_but_over_256k_not_proven",
        )
        return result
    provider_error = data.get("error")
    if isinstance(provider_error, dict):
        provider_error = provider_error.get("message")
    if not isinstance(provider_error, str):
        provider_error = data.get("resultMsg")
    if isinstance(provider_error, str):
        result["provider_error_excerpt"] = provider_error[:1200]
    # Interpret only the provider's explicit pair, not a generic 400 or catalog metadata.
    # Search serialized text too: LiteLLM may nest the originating error inside a string.
    match = re.search(
        r"input\s*\(\s*([\d,]+)\s*tokens?\s*\)\s*is longer than.{0,100}?context length\s*\(\s*([\d,]+)\s*tokens?\s*\)",
        body,
        re.IGNORECASE,
    )
    if match:
        sent, capacity = (int(part.replace(",", "")) for part in match.groups())
        result.update(
            provider_reported_input_tokens=sent,
            provider_reported_context_tokens=capacity,
            input_over_256k_verified=sent > THRESHOLD,
            accepted_over_256k=False,
            verdict="context_length_rejected",
        )
    return result


def run(route, body, directory, timeout, *, send=post_once):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / "request.json").write_bytes(body)
    url = route["base_url"].rstrip("/") + "/chat/completions"
    report = {
        "check": "synthetic_long_input_probe",
        "route": "direct_configured_upstream_bypassing_local_proxies_and_demo_budgets",
        "model_alias": ALIAS,
        "upstream_model": route["upstream_model"],
        "endpoint": url,
        "source_sha256": route["source_sha256"],
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "threshold_tokens": THRESHOLD,
        "input_bytes": len(body),
        "max_input_tokens_parameter_sent": False,
        "max_tokens_parameter_sent": False,
        "http_post_attempts": 1,
        "client_retries": 0,
        "upstream_attempts": None,
        "application_code_updated": False,
        "configuration_modified": False,
        "redis_calls": 0,
        "database_calls": 0,
        "output_dir": str(directory),
        "evidence_scope": "this_upstream_route_and_request_only",
        "input_over_256k_verified": False,
        "accepted_over_256k": None,
        "verdict": "request_started_outcome_unknown",
    }

    def save():
        (directory / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    save()
    started = time.monotonic()
    try:
        code, raw = send(url, route["api_key"], body, timeout)
        safe = redact(raw, route["api_key"])
        (directory / "response.redacted.txt").write_text(safe, encoding="utf-8")
        report.update(assess(code, safe))
    except Exception as error:
        report.update(
            verdict="request_or_recording_failed_capacity_unknown",
            error_type=type(error).__name__,
        )
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path.home() / "sec_know_model"))
    parser.add_argument("--units", type=int, default=DEFAULT_UNITS)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    os.umask(0o077)
    if args.timeout <= 0:
        raise ProbeError("positive_timeout_required")
    route = load_route(Path(args.project_root).expanduser())
    payload = make_request(route["upstream_model"], args.units)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "model_alias": ALIAS,
                "model": route["upstream_model"],
                "endpoint": route["base_url"],
                "route_source": "PRD_MODEL_CONFIGS_literal",
                "route": "direct_upstream",
                "synthetic_units": args.units,
                "seed": SEED,
                "input_bytes": len(body),
                "threshold_tokens": THRESHOLD,
                "token_count_exact": False,
                "max_http_posts": 1,
                "client_retries": 0,
                "timeout_seconds": args.timeout,
                "max_input_tokens_parameter_sent": False,
                "max_tokens_parameter_sent": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print(
        "仅发送一次人工长文本，绕过本地代理与规则取样预算；不会修改模型配置。实际输入tokens以服务返回为准。",
        flush=True,
    )
    with (
        open("/dev/tty", "r", encoding="utf-8") as reader,
        open("/dev/tty", "w", encoding="utf-8") as writer,
    ):
        writer.write("确认发送请输入 LIVE，其他输入退出：")
        writer.flush()
        answer = reader.readline().strip()
    if answer != "LIVE":
        print("已取消，未发送请求。")
        return
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = (
        Path(tempfile.mkdtemp(prefix="context-input-probe-", dir=parent)) / "probe"
    )
    print("结果目录：" + str(directory), flush=True)
    print("开始唯一一次长输入请求，请等待服务返回；不会自动重试。", flush=True)
    report = run(route, body, directory, args.timeout)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    conclusions = {
        "accepted_over_256k": "本次服务成功接受了超过262144 tokens的输入。",
        "completed_but_over_256k_not_proven": "请求完成，但返回用量尚不能证明输入超过262144 tokens。",
        "context_length_rejected": "服务明确以输入超过上下文容量拒绝，实际数字见上方。",
    }
    print(
        conclusions.get(
            report["verdict"],
            "本次未测得容量；请贴回summary.json，不能把此错误直接解释为256K限制。",
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {
                    "check": "synthetic_long_input_probe",
                    "status": "setup_or_local_recording_failed",
                    "error_type": type(error).__name__,
                    "error_code": str(error) if isinstance(error, ProbeError) else None,
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
SOC_MULTITURN_BASELINE_PYTHON
cat > "$probe_temp/probe_multiturn.py" <<'SOC_MULTITURN_PROBE_PYTHON'
"""Three synthetic chat turns retaining the complete request/reply history."""

import argparse
import hashlib
import json
import os
import random
import tempfile
import time
from pathlib import Path

import probe as baseline

ROUNDS = 3
UNITS_PER_TURN = 180000
TARGET_INPUT_TOKENS = 500 * 1024
MAX_INPUT_TOKENS = 1048576
SYSTEM = {
    "role": "system",
    "content": "This is a multi-turn context-length test. Reply only OK on every turn.",
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def save_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def make_user_message(round_number, units=UNITS_PER_TURN):
    if units < 1 or round_number not in range(1, ROUNDS + 1):
        raise baseline.ProbeError("invalid_round_or_units")
    rng = random.Random(baseline.SEED + round_number)
    filler = " ".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=units))
    return {
        "role": "user",
        "content": f"Round {round_number}. New synthetic filler begins:\n{filler}\nEnd of new filler. Reply only OK.",
    }


def make_request(model, messages):
    return {
        "model": model,
        "messages": messages,
        "stream": False,
        "max_input_tokens": MAX_INPUT_TOKENS,
    }


def update_totals(report):
    successful = [
        row
        for row in report["round_results"]
        if row.get("valid_completion_received") and row.get("status") == "completed"
    ]
    prompt_counts = [row.get("usage", {}).get("prompt_tokens") for row in successful]
    known = [value for value in prompt_counts if value is not None]
    observed = [
        row.get(
            "provider_reported_input_tokens", row.get("usage", {}).get("prompt_tokens")
        )
        for row in report["round_results"]
    ]
    observed = [value for value in observed if value is not None]
    report.update(
        rounds_completed=len(successful),
        successful_prompt_tokens_sum=sum(known),
        successful_usage_missing_rounds=len(prompt_counts) - len(known),
        max_reported_request_input_tokens=max(observed, default=None),
        full_request_over_500k_observed=any(
            value > TARGET_INPUT_TOKENS for value in observed
        ),
        input_over_500k_accepted_verified=any(
            value > TARGET_INPUT_TOKENS for value in known
        ),
    )


def run(
    route,
    directory,
    units_per_turn=UNITS_PER_TURN,
    timeout=600,
    *,
    send=baseline.post_once,
    progress=None,
):
    if units_per_turn < 1 or timeout <= 0:
        raise baseline.ProbeError("positive_units_and_timeout_required")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {
        "check": "synthetic_multiturn_context_probe",
        "status": "running",
        "route": "direct_configured_upstream",
        "model_alias": baseline.ALIAS,
        "upstream_model": route["upstream_model"],
        "endpoint": route["base_url"].rstrip("/") + "/chat/completions",
        "source_sha256": route["source_sha256"],
        "conversation_mode": "full_messages_history_with_actual_assistant_replies",
        "history_trimmed": False,
        "history_summarized": False,
        "synthetic_assistant_replies_inserted": False,
        "new_user_units_per_turn": units_per_turn,
        "planned_new_user_units_total": units_per_turn * ROUNDS,
        "new_user_token_count_exact": False,
        "threshold_tokens": baseline.THRESHOLD,
        "target_context_tokens": TARGET_INPUT_TOKENS,
        "max_input_tokens_parameter_sent": True,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "parameter_effect_verified": False,
        "max_tokens_parameter_sent": False,
        "rounds_planned": ROUNDS,
        "round_results": [],
        "http_post_attempts": 0,
        "client_retries": 0,
        "upstream_attempts": None,
        "stop_reason": None,
        "stopped_at_round": None,
        "usage_sum_is_not_context_size": True,
        "application_code_updated": False,
        "configuration_modified": False,
        "redis_calls": 0,
        "database_calls": 0,
        "history_semantic_retention_verified": False,
        "output_dir": str(directory),
    }
    history = [dict(SYSTEM)]
    update_totals(report)
    save_json(directory / "summary.json", report)
    for number in range(1, ROUNDS + 1):
        user = make_user_message(number, units_per_turn)
        history.append(user)
        body = encode(make_request(route["upstream_model"], history))
        folder = directory / f"round-{number:02d}"
        folder.mkdir(mode=0o700)
        (folder / "request.json").write_bytes(body)
        row = {
            "round": number,
            "status": "request_started_outcome_unknown",
            "new_user_units": units_per_turn,
            "new_user_content_bytes": len(user["content"].encode("utf-8")),
            "cumulative_new_user_units": units_per_turn * number,
            "full_request_bytes": len(body),
            "message_count": len(history),
            "prior_assistant_replies_in_request": number - 1,
            "request_sha256": hashlib.sha256(body).hexdigest(),
            "max_input_tokens": MAX_INPUT_TOKENS,
            "max_input_tokens_parameter_sent": True,
            "max_tokens_parameter_sent": False,
            "http_post_attempts": 1,
        }
        report["round_results"].append(row)
        report["http_post_attempts"] += 1
        save_json(folder / "summary.json", row)
        save_json(directory / "summary.json", report)
        if progress:
            progress(
                {
                    "round": number,
                    "status": "request_starting",
                    "new_user_units": units_per_turn,
                    "full_request_bytes": len(body),
                    "message_count": len(history),
                }
            )
        started = time.monotonic()
        try:
            code, raw = send(report["endpoint"], route["api_key"], body, timeout)
            safe = baseline.redact(raw, route["api_key"])
            (folder / "response.redacted.txt").write_text(safe, encoding="utf-8")
            row.update(baseline.assess(code, safe))
            if not row["valid_completion_received"]:
                row["status"] = "failed"
                report["stop_reason"] = row["verdict"]
            else:
                content = json.loads(raw)["choices"][0]["message"]["content"]
                if baseline.redact(content, route["api_key"]) != content:
                    raise baseline.ProbeError(
                        "redacted_reply_cannot_be_reused_unchanged"
                    )
                history.append({"role": "assistant", "content": content})
                row["status"] = "completed"
                row["assistant_content_sha256"] = hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest()
                row["assistant_content_bytes"] = len(content.encode("utf-8"))
        except Exception as error:
            row.update(status="failed", error_type=type(error).__name__)
            report["stop_reason"] = "request_or_recording_failed"
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        if report["stop_reason"]:
            report["status"] = "stopped"
            report["stopped_at_round"] = number
        update_totals(report)
        save_json(folder / "summary.json", row)
        save_json(directory / "summary.json", report)
        if progress:
            progress(row)
        if report["stop_reason"]:
            break
    if not report["stop_reason"]:
        report["status"] = "completed"
    if report["input_over_500k_accepted_verified"]:
        report["verdict"] = "full_conversation_input_over_500k_accepted"
    elif report["stop_reason"]:
        report["verdict"] = "stopped_without_accepting_over_500k"
    else:
        report["verdict"] = "rounds_completed_but_over_500k_not_proven"
    save_json(directory / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path.home() / "sec_know_model"))
    parser.add_argument("--units-per-turn", type=int, default=UNITS_PER_TURN)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    os.umask(0o077)
    if args.units_per_turn < 1 or args.timeout <= 0:
        raise baseline.ProbeError("positive_units_and_timeout_required")
    route = baseline.load_route(Path(args.project_root).expanduser())
    preview = {
        "status": "ready_for_multiturn_probe",
        "model_alias": baseline.ALIAS,
        "model": route["upstream_model"],
        "endpoint": route["base_url"],
        "route": "direct_upstream",
        "conversation_mode": "full_messages_history_with_actual_assistant_replies",
        "rounds": ROUNDS,
        "new_user_units_per_turn": args.units_per_turn,
        "planned_cumulative_user_units": [
            args.units_per_turn * i for i in range(1, ROUNDS + 1)
        ],
        "estimated_new_input_tokens_per_turn_approx": args.units_per_turn,
        "token_estimation_basis": "previous_probe_320000_letter_units_reported_320029_prompt_tokens",
        "new_user_token_count_exact": False,
        "target_context_tokens": TARGET_INPUT_TOKENS,
        "threshold_tokens": baseline.THRESHOLD,
        "max_input_tokens_parameter_sent": True,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_tokens_parameter_sent": False,
        "max_http_posts": ROUNDS,
        "client_retries": 0,
        "stop_on_first_failure": True,
        "timeout_seconds_per_request": args.timeout,
    }
    print(json.dumps(preview, ensure_ascii=False, indent=2), flush=True)
    print(
        f"三轮各新增{args.units_per_turn}个人工字母单元，tokens预计约同量。每轮携带全部历史及真实回复，最多3次POST，失败即停，不裁剪或重试。",
        flush=True,
    )
    print(
        "继续显式传max_input_tokens=1048576，不设置输出token上限；500K目标按512000 tokens计算，实际计数以服务返回为准。",
        flush=True,
    )
    with (
        open("/dev/tty", "r", encoding="utf-8") as reader,
        open("/dev/tty", "w", encoding="utf-8") as writer,
    ):
        writer.write("确认执行以上多轮测试请输入 LIVE，其他输入退出：")
        writer.flush()
        answer = reader.readline().strip()
    if answer != "LIVE":
        print("已取消，未发送请求。")
        return
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = (
        Path(tempfile.mkdtemp(prefix="context-multiturn-", dir=parent)) / "probe"
    )
    print("结果目录：" + str(directory), flush=True)
    report = run(
        route,
        directory,
        args.units_per_turn,
        args.timeout,
        progress=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report["input_over_500k_accepted_verified"]:
        print(
            "服务已返回某一完整请求输入超过512000 tokens的有效回答；此结果不证明模型完整利用了每一段历史。"
        )
    elif report["status"] == "stopped":
        print(
            "测试在第"
            + str(report["stopped_at_round"])
            + "轮停止，尚未证明超过500K完整对话可被接受；失败原因见该轮记录。"
        )
    else:
        print(
            "三轮已完成，但服务返回的单次输入用量尚未证明超过500K；不能用三轮用量相加代替上下文容量。"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {
                    "check": "synthetic_multiturn_context_probe",
                    "status": "setup_or_local_recording_failed",
                    "error_type": type(error).__name__,
                    "error_code": str(error)
                    if isinstance(error, baseline.ProbeError)
                    else None,
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
SOC_MULTITURN_PROBE_PYTHON
"$probe_project/.venv/bin/python" -B "$probe_temp/probe_multiturn.py" --project-root "$probe_project"
SOC_MULTITURN_CONTEXT
