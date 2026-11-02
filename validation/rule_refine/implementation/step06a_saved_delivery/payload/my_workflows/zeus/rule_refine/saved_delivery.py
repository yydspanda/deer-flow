"""保存不可变分析包及各次模拟交付记录；交付重试只读取已保存分析。"""

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .analysis import _invalid_constant, _unique_object
from .materials import _encode
from .proposal_check import check_proposal

LIMIT = 4 * 1024 * 1024


class SavedAnalysisError(ValueError):
    pass


class SavedAnalysisConflictError(SavedAnalysisError):
    pass


def _read(path):
    if path.is_symlink():
        raise SavedAnalysisError("不读取符号链接记录")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise SavedAnalysisError("记录类型或大小无效")
        content = stream.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise SavedAnalysisError("记录过大")
    return content


def _publish(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".analysis-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
            return False
        except FileExistsError:
            if _read(path) != content:
                raise SavedAnalysisConflictError("已有不同内容，原记录未覆盖") from None
            return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def _bundle(materials, analysis):
    if not isinstance(materials, dict) or materials.get("schema_version") != "sec_know.rule_materials.v1":
        raise SavedAnalysisError("需要已有材料记录")
    if not isinstance(analysis, dict) or analysis.get("schema_version") != "sec_know.rule_analysis.v1":
        raise SavedAnalysisError("需要已有分析结果")
    if (materials.get("mode"), materials.get("material_source")) not in (
        ("demo", "synthetic_fixture"),
        ("live", "workflow_task_store"),
        ("replay", "workflow_task_store"),
    ):
        raise SavedAnalysisError("材料模式与来源不一致")
    return _encode(
        {"schema_version": "sec_know.rule_analysis_bundle.v1", "materials": materials, "analysis": analysis}, LIMIT
    )


class LocalAnalysisStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()

    def _path(self, record_id):
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{64}", record_id):
            raise SavedAnalysisError("分析记录标识无效")
        return self.directory / "records" / (record_id + ".json")

    def save(self, materials, analysis):
        content = _bundle(materials, analysis)
        record_id = hashlib.sha256(content).hexdigest()
        path = self._path(record_id)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        duplicate = _publish(path, content)
        return {"record_id": record_id, "duplicate": duplicate, "saved_file": str(path)}

    def load(self, record_id):
        content = _read(self._path(record_id))
        if hashlib.sha256(content).hexdigest() != record_id:
            raise SavedAnalysisError("分析记录摘要不一致")
        try:
            data = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            if data.get("schema_version") != "sec_know.rule_analysis_bundle.v1":
                raise ValueError("未知版本")
            if _bundle(data["materials"], data["analysis"]) != content:
                raise ValueError("记录不是原始规范化内容")
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise SavedAnalysisError("分析记录格式无效") from None
        return data


def _validate_receipt(receipt, recommendation):
    required = {"recommendation_id", "delivery_mode", "status", "applied", "duplicate", "content_sha256"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise SavedAnalysisError("模拟接收回执格式不正确")
    _encode(receipt, 16 * 1024)
    if (
        receipt["recommendation_id"] != recommendation["recommendation_id"]
        or receipt["content_sha256"] != hashlib.sha256(_encode(recommendation, LIMIT)).hexdigest()
        or receipt["delivery_mode"] != "mock"
        or receipt["status"] != "received"
        or receipt["applied"] is not False
        or type(receipt["duplicate"]) is not bool
    ):
        raise SavedAnalysisError("模拟接收回执与原建议不一致")


def deliver_saved(store, record_id, load_rules, sink):
    """每次重读配置并记录检查，随后用同一建议 ID 提交；不含模型生成入口。

    接收异常或回执无效记 delivery_unconfirmed，不能推断接收端没有保存。
    结果写盘错误原样抛出；交付可能已发生，重试继续沿用 record_id。
    """
    bundle = store.load(record_id)
    checked = check_proposal(bundle["materials"], bundle["analysis"], load_rules)
    attempt_id = uuid4().hex
    (store.directory / "attempts").mkdir(mode=0o700, exist_ok=True)
    folder = store.directory / "attempts" / record_id
    request = {
        "schema_version": "sec_know.rule_delivery_attempt.v1",
        "record_id": record_id,
        "attempt_id": attempt_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "submitting" if checked["status"] == "reviewable" else checked["status"],
        "check": checked,
    }
    _publish(folder / (attempt_id + ".request.json"), _encode(request, LIMIT))
    result = {
        "schema_version": "sec_know.rule_delivery_result.v1",
        "record_id": record_id,
        "attempt_id": attempt_id,
        "status": checked["status"],
        "check_status": checked["status"],
        "check_reason": checked["reason"],
        "delivery_mode": "mock",
        "automatic_apply": False,
        "receipt": None,
    }
    if checked["status"] == "reviewable":
        recommendation = checked["recommendation"]
        try:
            receipt = sink.submit(copy.deepcopy(recommendation))
            _validate_receipt(receipt, recommendation)
            result.update(status="received", receipt=copy.deepcopy(receipt))
        except Exception as error:
            result.update(status="delivery_unconfirmed", error_type=type(error).__name__)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    _publish(folder / (attempt_id + ".result.json"), _encode(result, LIMIT))
    return result
