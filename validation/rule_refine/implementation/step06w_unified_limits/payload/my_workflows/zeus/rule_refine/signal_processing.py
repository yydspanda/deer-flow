"""单机信号分析串联；输入、分析结果留档，交付失败沿用原分析。"""

import copy
import fcntl
import hashlib
import json
import os
import stat
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .analysis import _invalid_constant, _unique_object, build_analysis_request
from .limits import get_limits
from .materials import _alert_id, _encode, _text, build_rule_materials
from .saved_delivery import (
    LIMIT,
    LocalAnalysisStore,
    SavedAnalysisError,
    _bundle,
    _publish,
    _read,
    deliver_saved,
)


def _load(path):
    raw = _read(path)
    try:
        value = json.loads(
            raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
        )
        if not isinstance(value, dict) or _encode(value, LIMIT) != raw:
            raise ValueError("记录不是规范化对象")
        return value
    except (ValueError, TypeError, RecursionError):
        raise SavedAnalysisError("信号处理记录损坏") from None


def _missing_tasks(signal, rows):
    wanted = {_text(value) for value in signal["sample_alert_ids"]}
    present = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("app_code") != signal["source_app"]
            or row.get("flow_id") != "alert_agent"
        ):
            continue
        try:
            present.add(_alert_id(row.get("alert_id")))
        except ValueError:
            continue
    return len(wanted - present)


def _result_unknown(analysis):
    transport = analysis.get("model_transport")
    return (
        isinstance(transport, dict)
        and transport.get("error_code") == "request_failed"
        and transport.get("sdk_request_calls", 0) > 0
        and transport.get("response_received") is not True
    )


class SignalProcessor:
    """注入配置工厂、任务读取、分析和接收端；本类不启动后台线程。

    load_task_rows(signal) 返回最多 100 个解码后的任务字典。
    analyze(materials) 返回第 4 步分析结果；实际调用限制由其适配器负责。
    本步只支持同一主机本地目录，不作为跨主机队列锁。
    """

    def __init__(
        self, directory, *, load_rules, load_task_rows, analyze, sink, source_kind
    ):
        if source_kind not in {"synthetic_fixture", "workflow_task_store"}:
            raise ValueError("未知材料来源")
        if not all(callable(value) for value in (load_rules, load_task_rows, analyze)):
            raise ValueError("配置、任务读取和分析必须可调用")
        self.directory = Path(directory).expanduser().resolve()
        self.store = LocalAnalysisStore(self.directory / "artifacts")
        self.load_rules, self.load_task_rows, self.analyze = (
            load_rules,
            load_task_rows,
            analyze,
        )
        self.sink, self.source_kind = sink, source_kind

    @contextmanager
    def _job(self, signal):
        signal = json.loads(_encode(signal, 64 * 1024))
        if (
            not isinstance(signal, dict)
            or signal.get("schema_version") != "sec_know.rule_burst_signal.v1"
        ):
            raise ValueError("需要 v1 突增信号")
        if (signal.get("mode"), self.source_kind) not in {
            ("demo", "synthetic_fixture"),
            ("live", "workflow_task_store"),
            ("replay", "workflow_task_store"),
        }:
            raise ValueError("信号模式与材料来源不一致")
        scope = {
            key: _text(signal.get(key))
            for key in (
                "mode",
                "namespace",
                "source_app",
                "company_code",
                "topic",
                "rule_code",
                "signal_id",
            )
        }
        job_id = hashlib.sha256(_encode(scope, 16 * 1024)).hexdigest()
        folder = self.directory / "jobs" / job_id
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if folder.is_symlink():
            raise SavedAnalysisError("信号目录不能为符号链接")
        fd = os.open(
            folder / "process.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        with os.fdopen(fd, "r+b") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise SavedAnalysisError("锁文件类型无效")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield folder, job_id, signal, False
                return
            try:
                _publish(
                    folder / "input.json",
                    _encode(
                        {
                            "schema_version": "sec_know.rule_signal_input.v1",
                            "signal": signal,
                            "source_kind": self.source_kind,
                        },
                        LIMIT,
                    ),
                )
                yield folder, job_id, signal, True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def process(self, signal):
        with self._job(signal) as (folder, job_id, signal, acquired):
            if not acquired:
                return self._summary("busy", job_id, False, 0)
            return self._process(folder, job_id, signal)

    def _attempt(self, folder):
        directory = folder / "attempts"
        if directory.is_symlink():
            raise SavedAnalysisError("尝试目录不能为符号链接")
        paths = sorted(directory.iterdir()) if directory.exists() else []
        if paths:
            if any(
                p.is_symlink()
                or not p.is_dir()
                or not re.fullmatch(r"[0-9]{4}", p.name)
                for p in paths
            ):
                raise SavedAnalysisError("尝试目录无效")
            # A crash between mkdir and publication can leave an empty directory.
            # Reuse that number on the next creation; a nonempty damaged attempt
            # must still be diagnosed instead of silently discarded.
            paths = [p for p in paths if any(p.iterdir())]
        if paths:
            current = paths[-1]
            number = int(current.name)
            if not (current / "attempt.json").is_file():
                raise SavedAnalysisError("尝试记录缺失")
            meta = _load(current / "attempt.json")
            if (
                meta.get("schema_version") != "sec_know.rule_attempt.v1"
                or type(meta.get("number")) is not int
                or meta["number"] != number
                or type(meta.get("budget_start")) is not int
                or not 1 <= meta["budget_start"] <= number
                or meta.get("authorization") not in {"initial", "operator", "automatic"}
            ):
                raise SavedAnalysisError("尝试记录无效")
            return current, number, meta
        if (folder / "analysis_started.json").exists() or (
            folder / "analysis.json"
        ).exists():
            return folder, 0, None
        return None, 0, None

    def _new_attempt(self, folder, number, budget_start, authorization):
        if not 1 <= number <= 9999:
            raise SavedAnalysisError("尝试数量超出可记录范围")
        current = folder / "attempts" / f"{number:04d}"
        meta = {
            "schema_version": "sec_know.rule_attempt.v1",
            "number": number,
            "budget_start": budget_start,
            "authorization": authorization,
        }
        _publish(current / "attempt.json", _encode(meta, 8192))
        return current, number, meta

    def request_retry(self, signal, *, allow_unconfirmed=False):
        """Schedule one new analysis series; no model/Redis calls in this method."""
        if type(allow_unconfirmed) is not bool:
            raise ValueError("未知结果授权必须明确")
        with self._job(signal) as (folder, job_id, signal, acquired):
            if not acquired:
                return {"status": "busy", "job_id": job_id, "model_calls": 0}
            current, number, meta = self._attempt(folder)
            if current is None:
                raise SavedAnalysisError("没有可重试的已有分析")
            result = current / "analysis.json"
            started = current / "analysis_started.json"
            if not started.exists() and not result.exists():
                if (current / "released.json").exists():
                    self._new_attempt(folder, number + 1, number + 1, "operator")
                    return {
                        "status": "scheduled",
                        "job_id": job_id,
                        "attempt_number": number + 1,
                        "model_calls": 0,
                        "effective_on": "next_background_tick",
                    }
                return {
                    "status": "already_scheduled",
                    "job_id": job_id,
                    "attempt_number": number,
                    "model_calls": 0,
                }
            unknown = started.exists() and not result.exists()
            if started.exists():
                self._started_materials(current, signal)
            bundle = self._saved_attempt(current, signal) if result.exists() else None
            unknown = unknown or (
                bundle is not None and _result_unknown(bundle["analysis"])
            )
            if unknown and not allow_unconfirmed:
                raise SavedAnalysisError(
                    "analysis_unconfirmed_requires_explicit_authorization"
                )
            if not unknown:
                failed = bundle["analysis"].get("status") == "analysis_failed"
                if not failed:
                    saved = self.store.save(bundle["materials"], bundle["analysis"])
                    deliveries = self.store.directory / "attempts" / saved["record_id"]
                    results = (
                        list(deliveries.glob("*.result.json"))
                        if deliveries.exists()
                        else []
                    )
                    last_status = (
                        _load(max(results, key=lambda p: p.stat().st_mtime_ns)).get(
                            "status"
                        )
                        if results
                        else None
                    )
                    if last_status in {"received", "not_applicable", "manual_review"}:
                        raise SavedAnalysisError(
                            "only_failed_or_blocked_analysis_can_be_retried"
                        )
                    if last_status != "blocked":
                        return {
                            "status": "delivery_retry_scheduled",
                            "job_id": job_id,
                            "attempt_number": number,
                            "model_calls": 0,
                            "may_call_model_on_next_tick": False,
                        }
            self._new_attempt(folder, number + 1, number + 1, "operator")
            return {
                "status": "scheduled",
                "job_id": job_id,
                "attempt_number": number + 1,
                "previous_attempt": number,
                "previous_result_unknown": unknown,
                "model_calls": 0,
                "effective_on": "next_background_tick",
            }

    def release(self, signal, *, allow_unconfirmed=False):
        """Durably release a failed/waiting signal without creating a recommendation."""
        if type(allow_unconfirmed) is not bool:
            raise ValueError("未知结果授权必须明确")
        with self._job(signal) as (folder, job_id, signal, acquired):
            if not acquired:
                return {"status": "busy", "job_id": job_id, "model_calls": 0}
            current, number, _ = self._attempt(folder)
            current = folder if current is None else current
            unknown = (current / "analysis_started.json").exists() and not (
                current / "analysis.json"
            ).exists()
            if (current / "analysis.json").exists():
                unknown = _result_unknown(
                    self._saved_attempt(current, signal)["analysis"]
                )
            if unknown and not allow_unconfirmed:
                raise SavedAnalysisError(
                    "analysis_unconfirmed_requires_explicit_authorization"
                )
            marker = {
                "schema_version": "sec_know.rule_signal_release.v1",
                "job_id": job_id,
                "attempt_number": number,
                "signal": signal,
                "reason": "operator_released_without_recommendation",
                "automatic_apply": False,
                "previous_result_unknown": unknown,
            }
            _publish(current / "released.json", _encode(marker, LIMIT))
            return {
                **self._summary("released", job_id, True, 0),
                "attempt_number": number,
            }

    def analysis_path(self, job_id, attempt_number=0):
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{64}", job_id):
            raise SavedAnalysisError("信号目录身份无效")
        if type(attempt_number) is not int or not 0 <= attempt_number <= 9999:
            raise SavedAnalysisError("尝试身份无效")
        folder = self.directory / "jobs" / job_id
        return (
            folder / "analysis.json"
            if not attempt_number
            else folder / "attempts" / f"{attempt_number:04d}" / "analysis.json"
        )

    @staticmethod
    def _summary(status, job_id, reused, calls):
        return {
            "schema_version": "sec_know.rule_signal_processing.v1",
            "status": status,
            "job_id": job_id,
            "analysis_reused": reused,
            "model_calls_this_attempt": calls,
            "automatic_apply": False,
        }

    def _check_binding(self, materials, signal):
        scope = {
            key: _text(signal.get(key))
            for key in (
                "signal_id",
                "namespace",
                "source_app",
                "company_code",
                "topic",
                "rule_code",
            )
        }
        window = {
            "start": datetime.fromisoformat(signal["window_start"]).isoformat(),
            "end": datetime.fromisoformat(signal["window_end"]).isoformat(),
            "count": signal["current_count"],
            "time_basis": signal.get("time_basis", "unknown"),
        }
        if (
            not isinstance(materials, dict)
            or materials.get("mode") != signal["mode"]
            or materials.get("material_source") != self.source_kind
            or materials.get("signal_scope") != scope
            or materials.get("window") != window
            or materials.get("requested_sample_count")
            != len({_text(value) for value in signal["sample_alert_ids"]})
        ):
            raise SavedAnalysisError("已保存材料与当前信号不一致")

    def _started_materials(self, current, signal):
        marker = _load(current / "analysis_started.json")
        if (
            set(marker) != {"schema_version", "materials"}
            or marker["schema_version"] != "sec_know.rule_signal_started.v1"
        ):
            raise SavedAnalysisError("分析开始记录无效")
        self._check_binding(marker["materials"], signal)
        return marker["materials"]

    def _saved_attempt(self, current, signal):
        materials = self._started_materials(current, signal)
        bundle = _load(current / "analysis.json")
        if (
            set(bundle) != {"schema_version", "materials", "analysis"}
            or bundle["schema_version"] != "sec_know.rule_analysis_bundle.v1"
            or bundle["materials"] != materials
        ):
            raise SavedAnalysisError("分析结果与原材料不一致")
        _bundle(bundle["materials"], bundle["analysis"])
        return bundle

    def _process(self, folder, job_id, signal):
        current, number, meta = self._attempt(folder)
        if current is None and (folder / "released.json").exists():
            return {**self._summary("released", job_id, True, 0), "attempt_number": 0}
        if current is not None and (current / "released.json").exists():
            return {
                **self._summary("released", job_id, True, 0),
                "attempt_number": number,
            }
        if current is None:
            current, number, meta = self._new_attempt(folder, 1, 1, "initial")
        elif number and (current / "analysis.json").exists():
            previous = self._saved_attempt(current, signal)
            used = number - meta["budget_start"] + 1
            if (
                previous["analysis"].get("status") == "analysis_failed"
                and not _result_unknown(previous["analysis"])
                and used < get_limits().retry_attempts
            ):
                current, number, meta = self._new_attempt(
                    folder, number + 1, meta["budget_start"], "automatic"
                )
        started, result = current / "analysis_started.json", current / "analysis.json"
        reused, calls = False, 0
        if os.path.lexists(started):
            self._started_materials(current, signal)
            if not os.path.lexists(result):
                return {
                    **self._summary("analysis_unconfirmed", job_id, False, None),
                    "attempt_number": number,
                    "retry_available": True,
                }
            bundle = self._saved_attempt(current, signal)
            materials, analysis, reused = bundle["materials"], bundle["analysis"], True
            _bundle(materials, analysis)
        else:
            if os.path.lexists(result):
                raise SavedAnalysisError("已有分析结果但缺少开始记录")
            try:
                rules = self.load_rules()
                rows = self.load_task_rows(copy.deepcopy(signal))
                materials = build_rule_materials(
                    signal, rules, rows, source_kind=self.source_kind
                )
                missing = _missing_tasks(signal, rows)
                if missing and not materials.get("samples"):
                    return {
                        **self._summary("materials_pending", job_id, False, 0),
                        "missing_task_count": missing,
                    }
                materials["missing_task_count"] = missing
                if build_analysis_request(materials) is None:
                    return self._summary("materials_unavailable", job_id, False, 0)
                # Verify both the start marker and result-wrapper allowance before a model call.
                _encode(
                    {
                        "schema_version": "sec_know.rule_signal_started.v1",
                        "materials": materials,
                    },
                    LIMIT,
                )
                _encode(
                    {
                        "schema_version": "sec_know.rule_analysis_bundle.v1",
                        "materials": materials,
                        "analysis": {
                            "reserved_output": "x"
                            * (get_limits().output_kib * 1024 + 262144)
                        },
                    },
                    LIMIT,
                )
            except Exception as error:
                return {
                    **self._summary("materials_failed", job_id, False, 0),
                    "error_type": type(error).__name__,
                }
            _publish(
                started,
                _encode(
                    {
                        "schema_version": "sec_know.rule_signal_started.v1",
                        "materials": materials,
                    },
                    LIMIT,
                ),
            )
            try:
                analysis = self.analyze(copy.deepcopy(materials))
                content = _bundle(materials, analysis)
            except Exception as error:
                return {
                    **self._summary("analysis_unconfirmed", job_id, False, None),
                    "attempt_number": number,
                    "retry_available": True,
                    "error_type": type(error).__name__,
                }
            _publish(result, content)
            value = analysis.get("model_calls")
            calls = value if type(value) is int and value >= 0 else None
        saved = self.store.save(materials, analysis)
        delivery = deliver_saved(
            self.store, saved["record_id"], self.load_rules, self.sink
        )
        status = delivery["status"]
        retryable = False
        if _result_unknown(analysis):
            status = "analysis_unconfirmed"
        elif analysis.get("status") == "analysis_failed":
            if not number:
                status = "blocked"
            elif number - meta["budget_start"] + 1 < get_limits().retry_attempts:
                status, retryable = "analysis_retryable", True
            else:
                status = "failed_finalized"
                _publish(
                    current / "finalized.json",
                    _encode(
                        {
                            "schema_version": "sec_know.rule_failure_finalized.v1",
                            "job_id": job_id,
                            "attempt_number": number,
                            "record_id": saved["record_id"],
                            "reason": "analysis_attempts_exhausted",
                            "automatic_apply": False,
                        },
                        LIMIT,
                    ),
                )
        return {
            **self._summary(status, job_id, reused, calls),
            "analysis_status": analysis.get("status"),
            "analysis_error_code": analysis.get("error_code"),
            "attempt_number": number,
            "automatic_retry": retryable,
            "retry_available": status
            in {"blocked", "failed_finalized", "analysis_unconfirmed"},
            "record_id": saved["record_id"],
            "missing_task_count": materials.get("missing_task_count", 0),
            "delivery": delivery,
        }
