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
