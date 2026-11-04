"""处理一个作用域的待处理信号；完成结果落盘后，按原信号身份确认。"""

import copy
import hashlib
import json
import re

from .analysis import build_analysis_request
from .materials import _encode, _text
from .signal_processing import _load

SCOPE = ("source_app", "company_code", "topic", "rule_code")


def _durable_result(processor, signal, outcome):
    verifier = getattr(processor, "verify_durable_result", None)
    if callable(verifier):
        if verifier(signal, outcome) is not True:
            raise ValueError("durable_result_not_verified")
        return
    if outcome.get("status") == "released":
        number, job_id = outcome.get("attempt_number", 0), outcome["job_id"]
        path = processor.analysis_path(job_id, number).with_name("released.json")
        marker = _load(path)
        scope = {
            key: _text(signal.get(key))
            for key in ("mode", "namespace", *SCOPE, "signal_id")
        }
        expected_id = hashlib.sha256(_encode(scope, 16 * 1024)).hexdigest()
        expected_input = {
            "schema_version": "sec_know.rule_signal_input.v1",
            "signal": signal,
            "source_kind": processor.source_kind,
        }
        if (
            job_id != expected_id
            or _load(processor.directory / "jobs" / job_id / "input.json")
            != expected_input
            or marker.get("schema_version") != "sec_know.rule_signal_release.v1"
            or marker.get("job_id") != job_id
            or marker.get("attempt_number") != number
            or marker.get("signal") != signal
            or marker.get("automatic_apply") is not False
            or marker.get("reason") != "operator_released_without_recommendation"
        ):
            raise ValueError("释放记录未持久保存或不一致")
        return
    delivery = outcome["delivery"]
    record_id, attempt_id = outcome["record_id"], delivery["attempt_id"]
    if not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("交付尝试身份无效")
    bundle = processor.store.load(record_id)
    processor._check_binding(bundle["materials"], signal)
    if (
        bundle["analysis"].get("status") != "analysis_failed"
        and build_analysis_request(bundle["materials"]) is None
    ):
        raise ValueError("原记录没有可分析材料")
    scope = {
        key: _text(signal.get(key))
        for key in ("mode", "namespace", *SCOPE, "signal_id")
    }
    job_id = hashlib.sha256(_encode(scope, 16 * 1024)).hexdigest()
    folder = processor.directory / "jobs" / job_id
    expected = {
        "schema_version": "sec_know.rule_signal_input.v1",
        "signal": signal,
        "source_kind": processor.source_kind,
    }
    if (
        outcome.get("job_id") != job_id
        or _load(folder / "input.json") != expected
        or _load(processor.analysis_path(job_id, outcome.get("attempt_number", 0)))
        != bundle
    ):
        raise ValueError("完整信号与保存的分析不一致")
    path = (
        processor.store.directory
        / "attempts"
        / record_id
        / (attempt_id + ".result.json")
    )
    if (
        delivery.get("schema_version") != "sec_know.rule_delivery_result.v1"
        or delivery.get("record_id") != record_id
        or (
            delivery.get("status") != outcome["status"]
            and outcome["status"] != "failed_finalized"
        )
        or delivery.get("automatic_apply") is not False
        or _load(path) != delivery
    ):
        raise ValueError("交付结果尚未完整保存或不一致")
    if outcome["status"] == "failed_finalized":
        final_path = processor.analysis_path(
            job_id, outcome["attempt_number"]
        ).with_name("finalized.json")
        final = _load(final_path)
        if bundle["analysis"].get("status") != "analysis_failed" or final != {
            "schema_version": "sec_know.rule_failure_finalized.v1",
            "job_id": job_id,
            "attempt_number": outcome["attempt_number"],
            "record_id": record_id,
            "reason": "analysis_attempts_exhausted",
            "automatic_apply": False,
        }:
            raise ValueError("失败记录未完整归档")


def consume_pending(counter, observation, processor):
    """只消费已有信号，不增加计数、不启动后台服务；确认失败不重做分析。"""
    report = {
        "schema_version": "sec_know.rule_pending_processing.v1",
        "acknowledged": False,
        "automatic_apply": False,
    }

    def result(status, error=None, **values):
        extra = {} if error is None else {"error_type": type(error).__name__}
        return {**report, "status": status, **extra, **values}

    try:
        observation = json.loads(_encode(observation, 64 * 1024))
        if not isinstance(observation, dict) or observation.get("mode") != counter.mode:
            raise ValueError("观察模式不一致")
        expected = {
            "mode": counter.mode,
            "namespace": counter.namespace,
            **{key: observation[key] for key in SCOPE},
        }
        if expected["mode"] not in {"demo", "replay", "live"} or any(
            _text(v) != v for v in expected.values()
        ):
            raise ValueError("作用域不规范")
    except Exception as error:
        return result("invalid_signal", error)
    try:
        signal = counter.pending(copy.deepcopy(observation))
    except Exception as error:
        return result("pending_read_failed", error)
    if signal is None:
        return result("idle")
    try:
        signal = json.loads(_encode(signal, 64 * 1024))
        if (
            not isinstance(signal, dict)
            or signal.get("schema_version") != "sec_know.rule_burst_signal.v1"
            or any(signal.get(key) != value for key, value in expected.items())
            or _text(signal.get("signal_id")) != signal["signal_id"]
        ):
            raise ValueError("信号作用域不一致")
    except Exception as error:
        return result("invalid_signal", error)
    try:
        outcome = processor.process(copy.deepcopy(signal))
        if (
            not isinstance(outcome, dict)
            or outcome.get("schema_version") != "sec_know.rule_signal_processing.v1"
        ):
            raise ValueError("处理结果格式无效")
    except Exception as error:
        return result("processing_failed", error)
    report["processing"] = outcome
    if outcome.get("status") not in {
        "received",
        "not_applicable",
        "manual_review",
        "failed_finalized",
        "released",
    }:
        return result("retained")
    try:
        _durable_result(processor, signal, outcome)
    except Exception as error:
        return result("result_unverified", error)
    try:
        acknowledged = counter.acknowledge(
            copy.deepcopy(observation), signal["signal_id"]
        )
        if type(acknowledged) is not bool:
            raise ValueError("确认返回值无效")
    except Exception as error:
        # 删除可能已经成功，不能断言 Redis 中仍有信号。
        return result("ack_unconfirmed", error, acknowledged=None)
    return result(
        "acknowledged" if acknowledged else "ack_not_applied", acknowledged=acknowledged
    )
