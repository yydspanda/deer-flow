"""本机后台轮转：当前规则目录内查找，每次最多消费一个信号。"""

import bisect
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .analysis import _invalid_constant, _unique_object
from .limits import get_limits
from .materials import _encode, _text
from .pending_signal import consume_pending
from .saved_delivery import _read

MAX_ATTEMPTS = 5


def _serialize(value):
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("后台状态超过大小上限")
    return raw


def _replace(path, value):
    raw = _serialize(value)
    fd, temporary = tempfile.mkstemp(prefix=".tick-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _locked(directory):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(
        directory / "tick.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    with os.fdopen(fd, "r+b") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError("后台锁文件类型无效")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        yield directory / "cursor.json"


def _catalog(rules):
    rows = rules.list_rules()
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("规则目录须为最多 5000 项的列表")
    scopes = {}
    for row in rows:
        scope = {
            key: _text(row.get(key)) for key in ("company_code", "topic", "rule_code")
        }
        if any(row[key] != value for key, value in scope.items()):
            raise ValueError("规则目录作用域不能含首尾空白")
        identity = hashlib.sha256(_encode(scope, 8192)).hexdigest()
        scopes[identity] = {
            "mode": "live",
            "source_app": "zeus",
            "company_scope_source": "rule_config_unique_match",
            **scope,
        }
    return scopes


def _state(path, namespace=None):
    expected = {"schema_version": "sec_know.rule_background_tick.v1", "mode": "live"}
    if not os.path.lexists(path):
        if namespace is None:
            return None
        return {**expected, "namespace": namespace, "after": "", "retry": {}}
    raw = _read(path)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("后台状态超过大小上限")
    value = json.loads(
        raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )
    if not isinstance(value, dict) or _serialize(value) != raw:
        raise ValueError("后台状态不是规范化对象")
    if set(value) != {*expected, "namespace", "after", "retry"} or any(
        value.get(k) != v for k, v in expected.items()
    ):
        raise ValueError("后台状态格式不一致")
    if _text(value["namespace"]) != value["namespace"] or (
        namespace is not None and value["namespace"] != namespace
    ):
        raise ValueError("后台状态作用域不一致")
    if not isinstance(value["after"], str) or (
        value["after"] and not re.fullmatch(r"[0-9a-f]{64}", value["after"])
    ):
        raise ValueError("后台游标无效")
    retry = value["retry"]
    if not isinstance(retry, dict) or len(retry) > 5000:
        raise ValueError("后台重试状态无效")
    for key, entry in retry.items():
        if (
            not re.fullmatch(r"[0-9a-f]{64}", key)
            or not isinstance(entry, dict)
            or set(entry) != {"signal_id", "attempts", "next_at", "parked"}
        ):
            raise ValueError("后台重试条目无效")
        if (
            _text(entry["signal_id"]) != entry["signal_id"]
            or type(entry["attempts"]) is not int
            or not 0 <= entry["attempts"] <= MAX_ATTEMPTS
            or type(entry["parked"]) is not bool
        ):
            raise ValueError("后台重试身份或次数无效")
        timestamp = entry["next_at"]
        if (
            type(timestamp) not in (int, float)
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            raise ValueError("后台重试时间无效")
    return value


def _snapshot(raw):
    if not isinstance(raw, (str, bytes)):
        raise ValueError("Redis 信号类型无效")
    bounded = raw[:65537].encode("utf-8") if isinstance(raw, str) else raw[:65537]
    invalid_id = "invalid:" + hashlib.sha256(bounded).hexdigest()
    if len(raw) > 65536 or len(bounded) > 65536:
        return invalid_id, None
    try:
        signal = json.loads(
            raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
        )
        _encode(signal, 65536)
        identity = _text(signal.get("signal_id"))
        if (
            identity != signal["signal_id"]
            or signal.get("schema_version") != "sec_know.rule_burst_signal.v1"
        ):
            raise ValueError("信号身份无效")
        return identity, signal
    except (ValueError, TypeError, AttributeError, RecursionError):
        return invalid_id, None


class _ObservedCounter:
    def __init__(self, counter, signal):
        self.mode, self.namespace = counter.mode, counter.namespace
        self.counter, self.signal = counter, signal

    def pending(self, observation):
        return copy.deepcopy(self.counter._signal(copy.deepcopy(self.signal)))

    def acknowledge(self, observation, signal_id):
        return self.counter.acknowledge(observation, signal_id)


def reset_background_retry(directory, *, scopes=None):
    """显式操作才调用；清除重试暂停，不删除已有分析、交付或 Redis 信号。"""
    with _locked(directory) as path:
        if path is None:
            return {"status": "busy", "reset_scopes": 0}
        return _reset_locked(path, scopes=scopes)


def _reset_locked(path, *, scopes=None):
    """Caller owns the scheduler lock through pending restoration and state reset."""
    state = _state(path)
    if state is None:
        return {"status": "idle", "reset_scopes": 0}
    selected = set(state["retry"]) if scopes is None else set(scopes)
    count = len(selected & set(state["retry"]))
    state["retry"] = {
        key: value for key, value in state["retry"].items() if key not in selected
    }
    _replace(path, state)
    return {"status": "reset", "reset_scopes": count}


def run_tick(
    directory,
    *,
    rules,
    counter,
    processor,
    batch_size=256,
    retry_delay_seconds=None,
    now=None,
):
    """只依赖注入对象；无后台线程、模型构造或服务配置加载。"""
    limits = get_limits()
    retry_delay_seconds = (
        limits.retry_delay_seconds
        if retry_delay_seconds is None
        else retry_delay_seconds
    )
    max_attempts = limits.retry_attempts
    if counter.mode != "live" or _text(counter.namespace) != counter.namespace:
        raise ValueError("后台需要明确命名空间的 live 计数器")
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise ValueError("批次大小须为 1～256")
    if type(retry_delay_seconds) is not int or not 1 <= retry_delay_seconds <= 3600:
        raise ValueError("重试间隔须为 1～3600 秒")
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError("轮询时间无效")
    with _locked(directory) as path:
        if path is None:
            return {"status": "busy", "processed": 0, "automatic_apply": False}
        scopes = _catalog(rules)
        state = _state(path, counter.namespace)
        state["retry"] = {
            k: entry for k, entry in state["retry"].items() if k in scopes
        }
        keys = sorted(scopes)
        report = {
            "status": "idle",
            "processed": 0,
            "catalog_scopes": len(keys),
            "scanned": 0,
            "deferred": 0,
            "needs_attention": 0,
            "automatic_apply": False,
        }
        if not keys:
            _replace(path, state)
            return report
        start = bisect.bisect_right(keys, state["after"]) % len(keys)
        batch = (keys[start:] + keys[:start])[:batch_size]
        with counter.client.pipeline(transaction=False) as pipe:
            for key in batch:
                pipe.get(counter._keys(scopes[key])[2])
            found = pipe.execute()
        if not isinstance(found, list) or len(found) != len(batch):
            raise ValueError("待处理信号查询结果无效")
        report["scanned"] = len(batch)
        chosen, signal = None, None
        for key, raw in zip(batch, found):
            if raw is None:
                state["retry"].pop(key, None)
                continue
            identity, current = _snapshot(raw)
            entry = state["retry"].get(key)
            if entry is None or entry["signal_id"] != identity:
                entry = {
                    "signal_id": identity,
                    "attempts": 0,
                    "next_at": 0,
                    "parked": False,
                }
                state["retry"][key] = entry
            if entry["parked"] or entry["attempts"] >= max_attempts:
                entry["parked"] = True
                report["needs_attention"] += 1
            elif entry["next_at"] > now:
                report["deferred"] += 1
            else:
                chosen, signal = key, current
                break
        state["after"] = chosen or batch[-1]
        if chosen is None:
            _replace(path, state)
            if report["needs_attention"]:
                report["status"] = "needs_attention"
            return report
        entry = state["retry"][chosen]
        if signal is None:
            entry["parked"] = True
            _replace(path, state)
            return {
                **report,
                "status": "needs_attention",
                "processing_status": "invalid_signal",
                "needs_attention": report["needs_attention"] + 1,
            }
        entry["attempts"] += 1
        entry["next_at"] = now + retry_delay_seconds
        _replace(path, state)
        outcome = consume_pending(
            _ObservedCounter(counter, signal), scopes[chosen], processor
        )
        processing = outcome.get("processing", {})
        status = outcome["status"]
        if processing.get("status") in {"capacity_wait", "materials_pending"}:
            entry["attempts"] -= 1
        elif processing.get("status") == "analysis_retryable":
            # Analysis attempts have their own durable budget. Earlier material waits
            # must not consume that budget or park an otherwise retryable result.
            entry["attempts"] = 0
        elif status in {"acknowledged", "idle", "ack_not_applied"}:
            state["retry"].pop(chosen, None)
        elif (
            status in {"invalid_signal", "result_unverified"}
            or processing.get("status") in {"blocked", "analysis_unconfirmed"}
            or entry["attempts"] >= max_attempts
        ):
            entry["parked"] = True
        _replace(path, state)
        error_type = outcome.get("error_type") or processing.get("error_type")
        return {
            **report,
            "status": "needs_attention" if entry["parked"] else status,
            "needs_attention": report["needs_attention"] + int(entry["parked"]),
            "processed": 1,
            "scope": {
                key: scopes[chosen][key]
                for key in ("company_code", "topic", "rule_code")
            },
            "processing_status": processing.get("status"),
            "acknowledged": outcome.get("acknowledged"),
            "attempts": entry["attempts"],
            "model_calls_this_attempt": processing.get("model_calls_this_attempt"),
            **({"error_type": error_type} if isinstance(error_type, str) else {}),
        }
