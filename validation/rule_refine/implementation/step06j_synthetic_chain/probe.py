"""Isolated synthetic count-to-analysis check; clients are supplied by the caller."""

import copy
import hashlib
import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def synthetic_data(identity=None):
    """Return one synthetic rule, three task rows and three matching requests."""
    identity = uuid4().hex if identity is None else identity
    if (
        not isinstance(identity, str)
        or not 1 <= len(identity) <= 64
        or identity != identity.strip()
    ):
        raise ValueError("Invalid synthetic identity")
    configuration = {
        "topic": "demo-http",
        "expression": "A",
        "A": {"name": "path", "operator": "contain", "value": "/"},
        "description": "人工演示规则：HTTP路径包含斜线即触发；仅用于隔离链路测试。",
    }
    digest = hashlib.sha256(
        json.dumps(configuration, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    rule = {
        "company_code": "demo-company",
        "topic": "demo-http",
        "rule_code": "DEMO_RULE_REFINE_HTTP_HEALTH",
        "rule_name": "人工演示：HTTP健康探测集中触发",
        "rule_config_version": "synthetic-v1:sha256:" + digest,
        "source": {"kind": "synthetic_fixture"},
        "configuration": configuration,
    }
    rows, payloads = [], []
    for number in range(3):
        alert_id = f"DEMO-{identity}-{number}"
        data = {
            "alert": {
                "alertId": alert_id,
                "hitLog": [
                    {
                        "topic": rule["topic"],
                        "ruleCode": rule["rule_code"],
                        "zeusRawLogs": [
                            {
                                "host": "synthetic.invalid",
                                "path": "/health",
                                "method": "GET",
                                "user_agent": "RuleRefineSyntheticProbe/1.0",
                                "source_ip": "192.0.2.10",
                                "scenario_note": "人工构造的周期健康探测；正常业务属性仍需运营确认。",
                            }
                        ],
                    }
                ],
            }
        }
        rows.append(
            {
                "id": "task-" + alert_id,
                "app_code": "zeus",
                "flow_id": "alert_agent",
                "alert_id": alert_id,
                "alert_data": copy.deepcopy(data),
                "agent_response": None,
            }
        )
        payloads.append(
            {
                "app_code": "zeus",
                "flow_id": "alert_agent",
                "session_id": "session-" + alert_id,
                "alert_id": alert_id,
                "alert_data": copy.deepcopy(data),
            }
        )
    return {"rule": rule, "rows": rows, "payloads": payloads}


class SyntheticRuleSource:
    def __init__(self, rule):
        self.rule = copy.deepcopy(rule)

    def get_rule(self, rule_code, *, company_code=None, topic=None):
        if rule_code != self.rule["rule_code"] or topic != self.rule["topic"]:
            raise ValueError("Synthetic rule identity mismatch")
        if company_code is not None and company_code != self.rule["company_code"]:
            raise ValueError("Synthetic company mismatch")
        return copy.deepcopy(self.rule)


def save(directory, name, value, *, text=False):
    raw = (
        value.encode("utf-8")
        if text
        else json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode(
            "utf-8"
        )
    )
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def report_text(summary, analysis, evaluation):
    def prose(value):
        return html.escape(
            str("本次未取得相应分析内容。" if value is None or value == "" else value)
        )

    lines = [
        "# 人工样本链路验证",
        "",
        "本报告使用人工规则与3条人工告警，仅验证功能串联；不代表真实告警效果。",
        "手工调用既有消费函数；未通过真实HTTP入口或Celery派发，未写业务数据库。",
        "建议仅本地模拟接收，未修改线上规则。测试Redis键会清理，结果文件保留。",
        "",
        "- 链路状态：" + prose(summary["status"]),
        "- 分析状态：" + prose(summary.get("analysis_status")),
        "- SDK请求次数：" + prose(summary.get("sdk_request_calls")),
        "- 请求模型：" + prose(summary.get("requested_model")),
        "- 返回模型标签：" + prose(summary.get("returned_model")),
        "- 测试键清理：" + str(summary.get("cleanup_ok")),
        "",
        "## 问题",
        "",
        prose(analysis.get("issue")),
        "",
        "## 建议与前提",
        "",
        prose(analysis.get("advice")),
        "",
        prose(analysis.get("certainty")),
        "",
    ]
    recommendation = analysis.get("recommendation")
    if isinstance(recommendation, dict):
        lines += [
            "## 建议条件",
            "",
            "```json",
            json.dumps(recommendation.get("filter"), ensure_ascii=False, indent=2),
            "```",
            "",
            "仅为新增排除场景建议；需要运营核对及ZEUS引擎兼容验证。",
            "",
        ]
    else:
        lines += ["本次未形成可交付的新增排除条件，以上分析结论仍保留。", ""]
    if evaluation:
        lines += [
            "## 人工样本试算",
            "",
            "```json",
            json.dumps(evaluation, ensure_ascii=False, indent=2),
            "```",
            "",
            "这是样本匹配情况，不是误报率或线上降量率。",
            "",
        ]
    lines += [
        "## 执行摘要",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def run_check(
    directory,
    *,
    modules,
    redis_client,
    analyze,
    wrap_processor=lambda processor: processor,
    case=None,
):
    directory = Path(directory).expanduser()
    summary = {
        "check": "rule_refine_synthetic_chain",
        "mode": "demo",
        "material_source": "synthetic_fixture",
        "status": "failed",
        "automatic_apply": False,
        "real_ingress_connected": False,
        "celery_dispatch_tested": False,
        "business_database_calls": 0,
        "analysis_calls": 0,
        "sdk_request_calls": 0,
        "model_calls": 0,
        "output_dir": str(directory.absolute()),
        "cleanup_ok": True,
    }
    if directory.is_symlink() or (
        directory.exists() and (not directory.is_dir() or any(directory.iterdir()))
    ):
        return {
            **summary,
            "error_type": "ValueError",
            "failure_stage": "output_directory_not_empty_or_invalid",
        }
    keys, counts, observed_analysis, evaluation = set(), [], {}, {}
    claimed = False
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = directory.resolve()
        os.chmod(directory, 0o700)
        save(
            directory,
            "probe-start.json",
            {"mode": "demo", "material_source": "synthetic_fixture"},
        )
        claimed = True
        data = (
            synthetic_data()
            if case is None
            else json.loads(modules.analysis._encode(case, 128 * 1024))
        )
        if not isinstance(data, dict) or set(data) != {"rule", "rows", "payloads"}:
            raise ValueError("Invalid synthetic case")
        if data["rule"].get("source", {}).get("kind") != "synthetic_fixture" or any(
            not isinstance(data[key], list) or len(data[key]) != 3
            for key in ("rows", "payloads")
        ):
            raise ValueError("Three synthetic rows and requests are required")
        save(directory, "synthetic-input.json", data)
        save(directory, "rule.json", data["rule"])
        rules = SyntheticRuleSource(data["rule"])
        counter = modules.counter.RedisRuleCounter(
            redis_client, mode="demo", minimum_count=3
        )
        if counter.mode != "demo":
            raise ValueError("Isolated demo counter required")
        summary["namespace"] = counter.namespace
        at = datetime.now(timezone.utc)
        observations = []
        requests = [*data["payloads"], copy.deepcopy(data["payloads"][0])]
        requests[-1]["session_id"] += "-repush"
        for index, request in enumerate(requests):
            normalized = modules.ingress.normalize_received_alert(
                request, rules, received_at=at + timedelta(seconds=index), mode="demo"
            )
            if normalized["status"] != "ready" or len(normalized["observations"]) != 1:
                raise ValueError("Synthetic observation is invalid")
            observation = normalized["observations"][0]
            keys.update(counter._keys(observation))
            observations.append(observation)
            counts.append(counter.observe(observation))
        save(directory, "observations.json", observations)
        signal = counter.pending(observations[-1])
        summary.update(
            submitted=len(counts),
            unique_count=counts[-1]["current_count"],
            new_signal_count=sum(item["triggered"] for item in counts),
            duplicate_added=counts[-1]["added"],
        )
        if not (
            len(keys) == 3
            and sum(item["added"] for item in counts) == 3
            and summary["unique_count"] == 3
            and summary["new_signal_count"] == 1
            and summary["duplicate_added"] is False
            and signal is not None
            and counts[2]["pending_signal"]["signal_id"]
            == counts[3]["pending_signal"]["signal_id"]
            == signal["signal_id"]
            and len(signal["sample_alert_ids"]) == 3
        ):
            raise ValueError("Synthetic counting assertions failed")
        save(directory, "signal.json", signal)

        def analyze_once(materials):
            if summary["analysis_calls"]:
                raise RuntimeError("Repeated model generation is not allowed")
            summary["analysis_calls"] += 1
            request = modules.analysis.build_analysis_request(materials)
            if request is None:
                raise ValueError("Synthetic materials cannot be analyzed")
            save(directory, "request.json", request)
            save(
                directory,
                "output-schema.json",
                modules.analysis.AnalysisDraft.model_json_schema(),
            )
            summary.update(model_calls=None, sdk_request_calls=None)
            result = analyze(copy.deepcopy(materials))
            observed_analysis.update(copy.deepcopy(result))
            return result

        processor = modules.signal_processing.SignalProcessor(
            directory / "processing",
            load_rules=lambda: SyntheticRuleSource(data["rule"]),
            load_task_rows=lambda signal: copy.deepcopy(data["rows"]),
            analyze=analyze_once,
            sink=modules.local_sink.LocalRecommendationSink(directory / "receipts"),
            source_kind="synthetic_fixture",
        )
        guarded = wrap_processor(processor)
        first = modules.pending_signal.consume_pending(
            counter, observations[-1], guarded
        )
        second = (
            modules.pending_signal.consume_pending(counter, observations[-1], guarded)
            if first["status"] == "acknowledged"
            else None
        )
        save(directory, "consumption.json", {"first": first, "second": second})
        processing = first.get("processing", {})
        summary.update(
            consumer_status=first["status"],
            processing_status=processing.get("status"),
            processing_error_type=processing.get("error_type")
            or first.get("error_type"),
            next_consume_status=second["status"] if second else None,
            acknowledged=first.get("acknowledged"),
            record_id=processing.get("record_id"),
        )
        record_id = summary["record_id"]
        if record_id:
            bundle = processor.store.load(record_id)
            observed_analysis = bundle["analysis"]
            delivery = processing["delivery"]
            attempt = (
                processor.store.directory
                / "attempts"
                / record_id
                / (delivery["attempt_id"] + ".request.json")
            )
            checked = json.loads(attempt.read_text(encoding="utf-8"))["check"]
            evaluation = checked.get("evaluation", {})
            summary["delivery_status"] = delivery["status"]
            summary["check_reason"] = delivery["check_reason"]
        audit = observed_analysis.get("model_transport", {})
        summary.update(
            analysis_status=observed_analysis.get("status"),
            analysis_error_code=observed_analysis.get("error_code"),
            model_error_code=audit.get("error_code"),
            model_error_type=audit.get("error_type"),
            sdk_request_calls=audit.get(
                "sdk_request_calls", summary["sdk_request_calls"]
            ),
            model_calls=observed_analysis.get("model_calls", summary["model_calls"]),
            requested_model=audit.get("requested_model"),
            returned_model=audit.get("returned_model"),
            transport_mode=audit.get("transport_mode"),
            finish_reason=audit.get("finish_reason"),
            usage=audit.get("usage"),
            elapsed_ms=audit.get("elapsed_ms"),
            upstream_attempts=audit.get("upstream_attempts"),
            sample_match_counts=evaluation.get("counts"),
        )
        if (
            first["status"] == "acknowledged"
            and second
            and second["status"] == "idle"
            and summary["analysis_calls"] == 1
            and summary["sdk_request_calls"] == 1
            and summary["analysis_status"]
            in {
                "proposal",
                "no_change",
                "insufficient_evidence",
                "needs_existing_condition_change",
            }
            and processing.get("status") in {"received", "not_applicable"}
        ):
            summary["status"] = "passed"
    except Exception as error:
        summary.update(status="failed", error_type=type(error).__name__)
    finally:
        # A returned model result may precede a later storage error. Do not lose its
        # actual request accounting, or report zero if the callback failed uncertainly.
        if observed_analysis:
            audit = observed_analysis.get("model_transport", {})
            summary.update(
                analysis_status=observed_analysis.get("status"),
                analysis_error_code=observed_analysis.get("error_code"),
                model_error_code=audit.get("error_code"),
                model_error_type=audit.get("error_type"),
                sdk_request_calls=audit.get("sdk_request_calls"),
                model_calls=observed_analysis.get("model_calls"),
                requested_model=audit.get("requested_model"),
                returned_model=audit.get("returned_model"),
                transport_mode=audit.get("transport_mode"),
                finish_reason=audit.get("finish_reason"),
                usage=audit.get("usage"),
                elapsed_ms=audit.get("elapsed_ms"),
                upstream_attempts=audit.get("upstream_attempts"),
            )
        cleanup_failed = []
        for key in sorted(keys):
            try:
                redis_client.delete(key)
                if redis_client.exists(key):
                    cleanup_failed.append(key)
            except Exception:
                cleanup_failed.append(key)
        summary["cleanup_ok"] = not cleanup_failed
        summary["test_keys_tracked"] = len(keys)
        if cleanup_failed:
            summary.update(status="failed", cleanup_failed_keys=cleanup_failed)
        if claimed:
            try:
                save(directory, "counts.json", counts)
                save(
                    directory,
                    "REPORT.md",
                    report_text(summary, observed_analysis, evaluation),
                    text=True,
                )
                save(directory, "summary.json", summary)
            except Exception as error:
                summary.update(
                    status="failed", artifact_error_type=type(error).__name__
                )
    return summary
