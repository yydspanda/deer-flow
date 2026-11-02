"""从显式指定的本地 SQLite 任务表只读获取信号样本；不加载应用配置。"""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .analysis import _invalid_constant, _unique_object
from .limits import ALERT_BYTES, TASK_ROWS, TASK_TOTAL_BYTES
from .materials import _encode, _text

ALERT_LIMIT = ALERT_BYTES
RESPONSE_LIMIT = ALERT_BYTES
TOTAL_LIMIT = TASK_TOTAL_BYTES


class TaskReadError(ValueError):
    pass


class TaskSampleLimitError(TaskReadError):
    pass


def _decode(raw, size, limit, *, require_object=False):
    if size is None:
        return None, False
    if raw is None or size > limit:
        return None, True
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if require_object and not isinstance(value, dict):
            raise ValueError("原告警不是对象")
        _encode(value, limit)
        return value, False
    except (ValueError, TypeError, AttributeError, RecursionError):
        return None, True


class LocalWorkflowTaskSource:
    """只按应用和引用告警 ID 取任务；规则与企业核对仍由材料组装负责。"""

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    def load_task_rows(self, signal):
        # 这里只校验查询所需字段；完整信号由材料组装校验。
        if (
            not isinstance(signal, dict)
            or signal.get("schema_version") != "sec_know.rule_burst_signal.v1"
            or signal.get("mode") not in {"live", "replay"}
            or signal.get("source_app") != "zeus"
        ):
            raise TaskReadError("需要 zeus 的 live/replay 信号")
        refs = signal.get("sample_alert_ids")
        if not isinstance(refs, list) or len(refs) > 20:
            raise TaskReadError("最多读取 20 个信号引用告警")
        refs = list(dict.fromkeys(_text(value) for value in refs))
        if not refs:
            return []
        if not self.path.is_file():
            raise TaskReadError("本地数据库文件不存在；未创建文件")
        placeholders = ",".join("?" for _ in refs)
        query = f"""
            SELECT id, app_code, flow_id, alert_id, status, created_date,
                   length(CAST(alert_data AS BLOB)) AS alert_size,
                   CASE WHEN length(CAST(alert_data AS BLOB)) <= ?
                        THEN CAST(alert_data AS BLOB) END AS alert_blob,
                   length(CAST(agent_response AS BLOB)) AS response_size,
                   CASE WHEN length(CAST(agent_response AS BLOB)) <= ?
                        THEN CAST(agent_response AS BLOB) END AS response_blob
            FROM uas_app_workflow_task_status
            WHERE app_code = ? AND flow_id = ? AND alert_id IN ({placeholders})
            ORDER BY created_date DESC, id DESC LIMIT {TASK_ROWS + 1}
        """
        result, total = [], 0
        try:
            with closing(
                sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
            ) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                deadline = time.monotonic() + 5
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                db.execute("BEGIN")
                cursor = db.execute(
                    query, (ALERT_LIMIT, RESPONSE_LIMIT, "zeus", "alert_agent", *refs)
                )
                for row in cursor:
                    if len(result) >= TASK_ROWS:
                        raise TaskSampleLimitError(
                            "引用告警对应任务超过 100 条；未截取部分记录"
                        )
                    total += sum(
                        len(row[key])
                        for key in ("alert_blob", "response_blob")
                        if row[key] is not None
                    )
                    if total > TOTAL_LIMIT:
                        raise TaskSampleLimitError(
                            "本次任务材料超过总读取预算；未返回部分记录"
                        )
                    alert, _ = _decode(
                        row["alert_blob"],
                        row["alert_size"],
                        ALERT_LIMIT,
                        require_object=True,
                    )
                    response, omitted = _decode(
                        row["response_blob"], row["response_size"], RESPONSE_LIMIT
                    )
                    result.append(
                        {
                            **{
                                key: row[key]
                                for key in (
                                    "id",
                                    "app_code",
                                    "flow_id",
                                    "alert_id",
                                    "status",
                                    "created_date",
                                )
                            },
                            "alert_data": alert,
                            "agent_response": response,
                            "agent_response_unavailable": omitted,
                        }
                    )
        except sqlite3.Error as error:
            raise TaskReadError("只读任务查询失败：" + type(error).__name__) from None
        return result
