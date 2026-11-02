"""单机信号分析串联；输入、分析结果留档，交付失败沿用原分析。"""

import copy
import fcntl
import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path

from .analysis import _invalid_constant, _unique_object
from .materials import _encode, _text, build_rule_materials
from .saved_delivery import LIMIT, LocalAnalysisStore, SavedAnalysisError, _bundle, _publish, _read, deliver_saved


def _load(path):
    raw = _read(path)
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        if not isinstance(value, dict) or _encode(value, LIMIT) != raw:
            raise ValueError("记录不是规范化对象")
        return value
    except (ValueError, TypeError, RecursionError):
        raise SavedAnalysisError("信号处理记录损坏") from None


class SignalProcessor:
    """注入配置工厂、任务读取、分析和接收端；本类不启动后台线程。

    load_task_rows(signal) 返回最多 100 个解码后的任务字典。
    analyze(materials) 返回第 4 步分析结果；实际调用限制由其适配器负责。
    本步只支持同一主机本地目录，不作为跨主机队列锁。
    """

    def __init__(self, directory, *, load_rules, load_task_rows, analyze, sink, source_kind):
        if source_kind not in {"synthetic_fixture", "workflow_task_store"}:
            raise ValueError("未知材料来源")
        if not all(callable(value) for value in (load_rules, load_task_rows, analyze)):
            raise ValueError("配置、任务读取和分析必须可调用")
        self.directory = Path(directory).expanduser().resolve()
        self.store = LocalAnalysisStore(self.directory / "artifacts")
        self.load_rules, self.load_task_rows, self.analyze = load_rules, load_task_rows, analyze
        self.sink, self.source_kind = sink, source_kind

    def process(self, signal):
        # 先复制有界输入；稳定身份定位目录，完整输入检查防止同一身份换内容。
        signal = json.loads(_encode(signal, 64 * 1024))
        if not isinstance(signal, dict) or signal.get("schema_version") != "sec_know.rule_burst_signal.v1":
            raise ValueError("需要 v1 突增信号")
        if (signal.get("mode"), self.source_kind) not in {
            ("demo", "synthetic_fixture"),
            ("live", "workflow_task_store"),
            ("replay", "workflow_task_store"),
        }:
            raise ValueError("信号模式与材料来源不一致")
        scope = {
            key: _text(signal.get(key))
            for key in ("mode", "namespace", "source_app", "company_code", "topic", "rule_code", "signal_id")
        }
        job_id = hashlib.sha256(_encode(scope, 16 * 1024)).hexdigest()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.directory / "jobs").mkdir(mode=0o700, exist_ok=True)
        folder = self.directory / "jobs" / job_id
        folder.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(folder / "process.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "r+b") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise SavedAnalysisError("锁文件类型无效")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self._summary("busy", job_id, False, 0)
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
                return self._process(folder, job_id, signal)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

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
            for key in ("signal_id", "namespace", "source_app", "company_code", "topic", "rule_code")
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
            or materials.get("requested_sample_count") != len({_text(value) for value in signal["sample_alert_ids"]})
        ):
            raise SavedAnalysisError("已保存材料与当前信号不一致")

    def _process(self, folder, job_id, signal):
        started, result = folder / "analysis_started.json", folder / "analysis.json"
        reused, calls = False, 0
        if os.path.lexists(started):
            marker = _load(started)
            if (
                set(marker) != {"schema_version", "materials"}
                or marker["schema_version"] != "sec_know.rule_signal_started.v1"
            ):
                raise SavedAnalysisError("分析开始记录无效")
            self._check_binding(marker["materials"], signal)
            if not os.path.lexists(result):
                return self._summary("analysis_unconfirmed", job_id, False, None)
            bundle = _load(result)
            if (
                set(bundle) != {"schema_version", "materials", "analysis"}
                or bundle["schema_version"] != "sec_know.rule_analysis_bundle.v1"
                or bundle["materials"] != marker["materials"]
            ):
                raise SavedAnalysisError("分析结果与原材料不一致")
            materials, analysis, reused = bundle["materials"], bundle["analysis"], True
            _bundle(materials, analysis)
        else:
            if os.path.lexists(result):
                raise SavedAnalysisError("已有分析结果但缺少开始记录")
            try:
                rules = self.load_rules()
                rows = self.load_task_rows(copy.deepcopy(signal))
                materials = build_rule_materials(signal, rules, rows, source_kind=self.source_kind)
            except Exception as error:
                return {**self._summary("materials_failed", job_id, False, 0), "error_type": type(error).__name__}
            _publish(
                started,
                _encode({"schema_version": "sec_know.rule_signal_started.v1", "materials": materials}, LIMIT),
            )
            try:
                analysis = self.analyze(copy.deepcopy(materials))
                content = _bundle(materials, analysis)
            except Exception as error:
                return {
                    **self._summary("analysis_unconfirmed", job_id, False, None),
                    "error_type": type(error).__name__,
                }
            # 先在本信号目录保存完整结果；后续索引或交付失败可从这里恢复。
            _publish(result, content)
            value = analysis.get("model_calls")
            calls = value if type(value) is int and value >= 0 else None
        saved = self.store.save(materials, analysis)
        delivery = deliver_saved(self.store, saved["record_id"], self.load_rules, self.sink)
        return {
            **self._summary(delivery["status"], job_id, reused, calls),
            "analysis_status": analysis.get("status"),
            "record_id": saved["record_id"],
            "delivery": delivery,
        }
