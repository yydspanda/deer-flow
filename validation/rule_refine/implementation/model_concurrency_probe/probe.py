#!/usr/bin/env python3
"""One synthetic concurrency burst, without importing the legacy application.

Python 3.10+ standard library only. Default: preview; --live permits one batch.
Run from sec_know_model, or set --project-root. No application configuration edits.
"""

import argparse
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

ALIAS = "Pub_DeepSeek_V4_Flash"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ProbeError(ValueError):
    """Fixed codes only; no source, credential or response text in diagnostics."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _dictionary(node):
    if not isinstance(node, ast.Dict):
        raise ProbeError("literal_dictionary_required")
    result = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ProbeError("literal_dictionary_keys_required")
        if key.value in result:
            raise ProbeError("duplicate_configuration_key")
        result[key.value] = value
    return result


def load_route(root):
    source = (Path(root) / "llm_service/openai_completion.py").read_bytes()
    matches = []
    for node in ast.parse(source.decode("utf-8-sig")).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(t, ast.Name) and t.id == "PRD_MODEL_CONFIGS" for t in targets
            ):
                matches.append(node.value)
    if len(matches) != 1:
        raise ProbeError("unique_PRD_MODEL_CONFIGS_required")
    fields = _dictionary(_dictionary(matches[0]).get(ALIAS))
    route = {}
    for key in ("type", "base_url", "upstream_model", "api_key"):
        node = fields.get(key)
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, str)
            or not node.value
        ):
            raise ProbeError("literal_OPENAI_route_required")
        route[key] = node.value
    url = urlsplit(route["base_url"])
    if (
        route["type"] != "OPENAI"
        or url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or any(c.isspace() or ord(c) < 32 for c in route["base_url"])
        or not all(32 < ord(c) < 127 for c in route["api_key"])
    ):
        raise ProbeError("invalid_OPENAI_route")
    route["source_sha256"] = hashlib.sha256(source).hexdigest()
    return route


def make_request(model, units, index, run_id, seed):
    rng = random.Random(f"{seed}:{run_id}:{index}")
    filler = " ".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=units))
    return {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": "Synthetic latency test. Reply only OK; no explanation.",
            },
            {
                "role": "user",
                "content": f"Request {run_id}-{index}. Filler follows:\n{filler}\nEnd. Reply only OK.",
            },
        ],
    }


def post_once(url, key, body, timeout):
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    # Each worker has its own connection/opener; no shared connection-pool limit.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    started = time.perf_counter()
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    headers_seconds = time.perf_counter() - started
    with response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        status = response.code
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProbeError("response_too_large")
    return status, raw, headers_seconds, time.perf_counter() - started


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def classify(http_status, raw_bytes):
    result = {
        "success": False,
        "answer_ok": False,
        "http_status": http_status,
        "finish_reason": None,
        "returned_model": None,
        "usage": {},
        "error_code": None,
    }
    if not 200 <= http_status < 300:
        result["error_code"] = f"http_{http_status}"
        return result
    try:
        data = json.loads(raw_bytes)
    except (ValueError, UnicodeError):
        result["error_code"] = "invalid_response_json"
        return result
    if not isinstance(data, dict):
        result["error_code"] = "invalid_response_shape"
        return result
    code = data.get("resultCode", "0")
    if (
        data.get("error") is not None
        or type(code) not in (str, int)
        or code not in ("0", 0)
        or data.get("success") is False
    ):
        result["error_code"] = "provider_business_error"
        return result
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    result["usage"] = {
        k: _integer(usage.get(k))
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    details = usage.get("prompt_tokens_details")
    result["usage"]["cached_tokens"] = (
        _integer(details.get("cached_tokens")) if isinstance(details, dict) else None
    )
    if isinstance(data.get("model"), str):
        result["returned_model"] = data["model"]
    choices = data.get("choices")
    choice = (
        choices[0]
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else {}
    )
    result["finish_reason"] = (
        choice.get("finish_reason")
        if isinstance(choice.get("finish_reason"), str)
        else None
    )
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    if result["finish_reason"] != "stop":
        result["error_code"] = "output_incomplete"
    elif not isinstance(content, str) or not content.strip():
        result["error_code"] = "empty_completion"
    else:
        result.update(success=True, answer_ok=content.strip() == "OK")
        result["answer_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        result["answer_bytes"] = len(content.encode("utf-8"))
    return result


def _stats(values):
    values = sorted(values)
    if not values:
        return None
    return {
        "min": round(values[0], 3),
        "p50": round(statistics.median(values), 3),
        "p95": round(values[math.ceil(len(values) * 0.95) - 1], 3),
        "max": round(values[-1], 3),
        "mean": round(statistics.mean(values), 3),
    }


def summarize(results, wall_seconds, peak_in_flight, launch_spread_seconds):
    successes = [r for r in results if r.get("success")]
    prompts = [r.get("usage", {}).get("prompt_tokens") for r in successes]
    prompts = [n for n in prompts if type(n) is int]
    return {
        "request_count": len(results),
        "success_count": len(successes),
        "failed_count": len(results) - len(successes),
        "answer_ok_count": sum(r.get("answer_ok", False) for r in results),
        "peak_in_flight": peak_in_flight,
        "peak_scope": "client_request_overlap_including_response_parsing_not_server_parallelism",
        "wall_seconds": round(wall_seconds, 3),
        "launch_spread_seconds": round(launch_spread_seconds, 6),
        "success_latency_seconds": _stats([r["elapsed_seconds"] for r in successes]),
        "all_request_latency_seconds": _stats([r["elapsed_seconds"] for r in results]),
        "prompt_tokens": _stats(prompts),
        "success_usage_missing_count": len(successes) - len(prompts),
        "completion_tokens": _stats(
            [
                r["usage"]["completion_tokens"]
                for r in successes
                if type(r.get("usage", {}).get("completion_tokens")) is int
            ]
        ),
        "http_status_counts": dict(Counter(str(r.get("http_status")) for r in results)),
        "error_counts": dict(
            Counter(r["error_code"] for r in results if r.get("error_code"))
        ),
        "batch_successes_per_second": round(len(successes) / wall_seconds, 3)
        if wall_seconds
        else None,
        "server_concurrency_verified": False,
        "celery_capacity_verified": False,
        "sustained_throughput_verified": False,
    }


def _save(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".probe-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Atomic publication without replacing an existing artifact.
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def run_batch(
    route,
    directory,
    *,
    concurrency=27,
    units=5000,
    timeout=600,
    seed=20261104,
    send=post_once,
):
    if (
        not 1 <= concurrency <= 27
        or not 1 <= units <= 200000
        or not 1 <= timeout <= 600
    ):
        raise ProbeError("invalid_probe_bounds")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    run_id = uuid.uuid4().hex
    requests = [
        make_request(route["upstream_model"], units, i + 1, run_id, seed)
        for i in range(concurrency)
    ]
    bodies = [
        json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        for r in requests
    ]
    _save(directory / "requests.json", requests)
    url = route["base_url"].rstrip("/") + "/chat/completions"
    metadata = {
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_alias": ALIAS,
        "upstream_model": route["upstream_model"],
        "route": "direct_configured_upstream_bypassing_Celery_and_local_proxies",
        "endpoint_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "source_sha256": route["source_sha256"],
        "concurrency": concurrency,
        "synthetic_units_per_request": units,
        "input_token_count_exact": False,
        "input_estimate_basis": "prior_same_route_letter_unit_probe_approximately_one_token_per_unit",
        "timeout_seconds": timeout,
        "timeout_scope": "socket_operations_not_hard_total_deadline",
        "max_tokens_parameter_sent": False,
        "thinking_parameters_sent": False,
        "client_retries": 0,
        "automatic_redirects": False,
        "warmup_calls": 0,
        "upstream_attempts": None,
        "max_HTTP_posts": concurrency,
        "attempt_count_scope": "client_call_attempts_not_proof_of_server_receipt",
        "redis_calls": 0,
        "database_calls": 0,
        "application_code_updated": False,
    }
    _save(directory / "preview.json", metadata)
    barrier = threading.Barrier(concurrency + 1)
    lock = threading.Lock()
    active, peak = 0, 0
    starts = []

    def worker(index, body):
        nonlocal active, peak
        barrier.wait(timeout=60)
        started = time.perf_counter()
        with lock:
            starts.append(started)
            active += 1
            peak = max(peak, active)
        try:
            status, raw, headers_time, body_time = send(
                url, route["api_key"], body, timeout
            )
            row = classify(
                status, raw.replace(route["api_key"].encode(), b"[credential omitted]")
            )
            row.update(
                response_headers_seconds=round(headers_time, 6),
                response_complete_seconds=round(body_time, 6),
            )
        except Exception as error:
            # Exception text and upstream bodies may contain credentials: retain class/code only.
            row = {
                "success": False,
                "answer_ok": False,
                "http_status": None,
                "usage": {},
                "error_code": (
                    "timeout"
                    if isinstance(error, TimeoutError)
                    or isinstance(getattr(error, "reason", None), TimeoutError)
                    else "transport_error"
                ),
                "error_type": type(error).__name__,
            }
        finally:
            ended = time.perf_counter()
            with lock:
                active -= 1
        row.update(
            index=index,
            http_post_attempts=1,
            elapsed_seconds=round(ended - started, 6),
            start_offset_seconds=round(started - batch_started, 6),
            end_offset_seconds=round(ended - batch_started, 6),
            request_sha256=hashlib.sha256(body).hexdigest(),
            request_bytes=len(body),
        )
        _save(directory / f"request-{index:02d}.json", row)
        return row

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker, i + 1, body) for i, body in enumerate(bodies)]
        batch_started = time.perf_counter()
        barrier.wait(timeout=60)
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            print(
                json.dumps(
                    {
                        k: row.get(k)
                        for k in (
                            "index",
                            "success",
                            "http_status",
                            "elapsed_seconds",
                            "usage",
                            "error_code",
                        )
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    # Report HTTP completion timing, excluding result serialization/printing time.
    wall_seconds = max(r["end_offset_seconds"] for r in results)
    report = dict(
        metadata, **summarize(results, wall_seconds, peak, max(starts) - min(starts))
    )
    report["check"] = "synthetic_model_concurrency_probe"
    report["status"] = (
        "completed" if report["failed_count"] == 0 else "completed_with_failures"
    )
    report["output_dir"] = str(directory)
    report["results"] = sorted(results, key=lambda r: r["index"])
    _save(directory / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--concurrency", type=int, default=27)
    parser.add_argument("--input-units", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument(
        "--output-parent", type=Path, default=Path.home() / "sec-rule-refine-data"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly send one batch; without this flag, preview only.",
    )
    args = parser.parse_args()
    if (
        not 1 <= args.concurrency <= 27
        or not 1 <= args.input_units <= 200000
        or not 1 <= args.timeout <= 600
    ):
        parser.error("concurrency must be 1..27; input-units 1..200000; timeout 1..600")
    try:
        route = load_route(args.project_root)
        print(
            json.dumps(
                {
                    "status": "ready",
                    "model_alias": ALIAS,
                    "upstream_model": route["upstream_model"],
                    "endpoint": route["base_url"],
                    "concurrent_requests": args.concurrency,
                    "synthetic_input_units_each": args.input_units,
                    "token_count_exact": False,
                    "expected_answer": "OK",
                    "max_HTTP_posts": args.concurrency,
                    "client_retries": 0,
                    "max_tokens_parameter_sent": False,
                    "route": "direct_upstream_not_Celery",
                    "live": args.live,
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        if not args.live:
            print("仅预览，未调用模型。增加 --live 后会直接发出上述一批请求。")
            return 0
        args.output_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = Path(
            tempfile.mkdtemp(prefix="model-concurrency-", dir=args.output_parent)
        )
        print("结果目录：" + str(parent / "probe"), flush=True)
        report = run_batch(
            route,
            parent / "probe",
            concurrency=args.concurrency,
            units=args.input_units,
            timeout=args.timeout,
        )
        print(
            json.dumps(
                {k: v for k, v in report.items() if k != "results"},
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 0 if report["failed_count"] == 0 else 1
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_code": str(error)
                    if isinstance(error, ProbeError)
                    else "probe_failed_inspect_saved_records",
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
