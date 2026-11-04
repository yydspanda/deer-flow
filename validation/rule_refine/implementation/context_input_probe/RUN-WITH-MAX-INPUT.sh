bash <<'SOC_EXPLICIT_MAX_INPUT'
set -euo pipefail
umask 077
probe_project="$HOME/sec_know_model"
probe_temp="$(mktemp -d "${TMPDIR:-/tmp}/sec-know-max-input.XXXXXX")"
trap 'rm -f "$probe_temp/probe.py" "$probe_temp/probe_with_max_input.py"; rmdir "$probe_temp"' EXIT
cat > "$probe_temp/probe.py" <<'SOC_BASELINE_PYTHON'
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
SOC_BASELINE_PYTHON
cat > "$probe_temp/probe_with_max_input.py" <<'SOC_MAX_INPUT_PYTHON'
"""Repeat the synthetic probe with an explicit max_input_tokens request field."""

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import probe as baseline

MAX_INPUT_TOKENS = 1048576


def encode(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def make_request(model, units):
    payload = baseline.make_request(model, units)
    payload["max_input_tokens"] = MAX_INPUT_TOKENS
    return payload


def request_audit(body):
    payload = json.loads(body)
    if (
        not isinstance(payload, dict)
        or type(payload.get("max_input_tokens")) is not int
        or payload["max_input_tokens"] != MAX_INPUT_TOKENS
        or set(payload) != {"model", "messages", "stream", "max_input_tokens"}
    ):
        raise baseline.ProbeError("explicit_max_input_tokens_request_required")
    original = {
        key: value for key, value in payload.items() if key != "max_input_tokens"
    }
    return {
        "max_input_tokens_parameter_sent": True,
        "max_input_tokens": payload["max_input_tokens"],
        "parameter_location": "HTTP_JSON_TOP_LEVEL",
        "parameter_effect_verified": False,
        "max_tokens_parameter_sent": False,
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "baseline_request_sha256": hashlib.sha256(encode(original)).hexdigest(),
        "input_bytes": len(body),
    }


def run(route, body, directory, timeout, *, send=baseline.post_once):
    audit = request_audit(body)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / "request.json").write_bytes(body)
    url = route["base_url"].rstrip("/") + "/chat/completions"
    report = {
        "check": "synthetic_long_input_explicit_max_input_probe",
        "route": "direct_configured_upstream_bypassing_local_proxies_and_demo_budgets",
        "model_alias": baseline.ALIAS,
        "upstream_model": route["upstream_model"],
        "endpoint": url,
        "source_sha256": route["source_sha256"],
        "threshold_tokens": baseline.THRESHOLD,
        **audit,
        "http_post_attempts": 1,
        "client_retries": 0,
        "upstream_attempts": None,
        "automatic_retry_without_parameter": False,
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
        safe = baseline.redact(raw, route["api_key"])
        (directory / "response.redacted.txt").write_text(safe, encoding="utf-8")
        report.update(baseline.assess(code, safe))
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
    parser.add_argument("--units", type=int, default=baseline.DEFAULT_UNITS)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    os.umask(0o077)
    if args.timeout <= 0:
        raise baseline.ProbeError("positive_timeout_required")
    route = baseline.load_route(Path(args.project_root).expanduser())
    body = encode(make_request(route["upstream_model"], args.units))
    print(
        json.dumps(
            {
                "status": "ready",
                "model_alias": baseline.ALIAS,
                "model": route["upstream_model"],
                "endpoint": route["base_url"],
                "route_source": "PRD_MODEL_CONFIGS_literal",
                "route": "direct_upstream",
                "synthetic_units": args.units,
                "seed": baseline.SEED,
                "threshold_tokens": baseline.THRESHOLD,
                "token_count_exact": False,
                "max_http_posts": 1,
                "client_retries": 0,
                "timeout_seconds": args.timeout,
                **request_audit(body),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print(
        "沿用同一份人工长输入，实际请求JSON顶层增加 max_input_tokens=1048576；只发一次，不设置输出token上限。",
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
        Path(tempfile.mkdtemp(prefix="context-max-input-", dir=parent)) / "probe"
    )
    print("结果目录：" + str(directory), flush=True)
    print("开始唯一一次请求：max_input_tokens=1048576。", flush=True)
    report = run(route, body, directory, args.timeout)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    conclusions = {
        "accepted_over_256k": "带max_input_tokens参数的本次请求成功接受超256K输入；服务是否因该参数改变限额仍需确认。",
        "context_length_rejected": "已显式携带max_input_tokens=1048576，服务仍按返回的上下文容量拒绝；参数是否被忽略尚未知。",
        "completed_but_over_256k_not_proven": "请求完成，但实际输入用量尚不能证明超过256K。",
    }
    print(
        conclusions.get(
            report["verdict"],
            "请求未完成，具体错误见provider_error_excerpt及response.redacted.txt；未去掉参数重试。",
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {
                    "check": "synthetic_long_input_explicit_max_input_probe",
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
SOC_MAX_INPUT_PYTHON
"$probe_project/.venv/bin/python" -B "$probe_temp/probe_with_max_input.py" --project-root "$probe_project"
SOC_EXPLICIT_MAX_INPUT
