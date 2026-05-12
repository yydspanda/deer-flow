"""yyds: 循环检测中间件 — 检测并打断 Agent 的重复工具调用死循环。

【做什么】在每次模型响应后，检查 Agent 是否陷入了重复调用同一组工具的死循环，并在必要时强制终止。
【为什么存在】这是 P0 级安全机制。大模型有时会反复调用相同的工具（相同参数），直到递归上限耗尽才停止，
   浪费大量 token 和时间。此中间件通过两层检测机制提前发现并打断循环。
【在链中的位置】after_model 阶段执行，即模型返回响应后、工具执行前介入。
【检测策略】
   第一层：基于哈希的精确匹配。对每次工具调用集（名称+关键参数）生成哈希，在滑动窗口内跟踪。
     - 达到 warn_threshold（默认3次）时注入警告消息，提示模型停止。
     - 达到 hard_limit（默认5次）时直接剥离所有 tool_calls，强制模型输出文本答案。
   第二层：基于工具类型频率检测。即使参数不同，同一个工具被调用太多次也会触发警告/强制停止。
     - 默认：同一工具调用30次警告，50次强制停止。
     - 支持按工具名自定义阈值（tool_freq_overrides），例如 bash 可以设更高的阈值。
【关键设计】
   - 使用线程安全的 OrderedDict + LRU 淘汰，最多追踪 max_tracked_threads（默认100）个线程。
   - 对 read_file 工具做了特殊处理：按行号分桶（200行一桶），避免读取相邻行被误判为重复。
   - 对 write_file/str_replace 工具使用完整参数哈希，因为同一文件可能被多次更新不同内容。
   - 目前将警告追加到 AIMessage.content 而非注入独立的 HumanMessage（临时方案，参见 #2724）。

---

Middleware to detect and break repetitive tool call loops.

P0 safety: prevents the agent from calling the same tool with the same
arguments indefinitely until the recursion limit kills the run.

Detection strategy:
  1. After each model response, hash the tool calls (name + args).
  2. Track recent hashes in a sliding window.
  3. If the same hash appears >= warn_threshold times, queue a
     "you are repeating yourself — wrap up" warning for the current
     thread/run. The warning is **injected at the next model call** (in
     ``wrap_model_call``) as a ``HumanMessage`` appended to the message
     list, *after* all ToolMessage responses to the previous
     AIMessage(tool_calls).
  4. If it appears >= hard_limit times, strip all tool_calls from the
     response so the agent is forced to produce a final text answer.

Why the warning is injected at ``wrap_model_call`` instead of
``after_model``:

  ``after_model`` fires immediately after the model emits an
  ``AIMessage`` that may carry ``tool_calls``. The tools node has not
  run yet, so no matching ``ToolMessage`` exists in the history. Any
  message we add here lands *between* the assistant's tool_calls and
  their responses. OpenAI/Moonshot reject the next request with
  ``"tool_call_ids did not have response messages"`` because their
  validators require the assistant's tool_calls to be followed
  immediately by tool messages. Anthropic also disallows mid-stream
  ``SystemMessage``. By deferring the warning to ``wrap_model_call``,
  every prior ToolMessage is already present in the request's message
  list and the warning is appended at the end — pairing intact, no
  ``AIMessage`` semantics are mutated.

Queued warnings are intentionally transient. If a run ends before the
next model request drains a queued warning, ``after_agent`` drops it
instead of carrying it into a later invocation for the same thread. The
hard-stop path still forces termination when the configured safety limit
is reached.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

if TYPE_CHECKING:
    from deerflow.config.loop_detection_config import LoopDetectionConfig

logger = logging.getLogger(__name__)

# yyds: 默认阈值 — 可通过 config.yaml 覆盖
_DEFAULT_WARN_THRESHOLD = 3  # 同一组工具调用重复 3 次后注入警告
_DEFAULT_HARD_LIMIT = 5  # 重复 5 次后强制停止（剥离 tool_calls）
_DEFAULT_WINDOW_SIZE = 20  # 滑动窗口大小：只看最近 20 次调用
_DEFAULT_MAX_TRACKED_THREADS = 100  # LRU 淘汰上限：最多追踪 100 个线程
_DEFAULT_TOOL_FREQ_WARN = 30  # 同一工具调用 30 次后频率警告
_DEFAULT_TOOL_FREQ_HARD_LIMIT = 50  # 同一工具调用 50 次后频率强制停止
_MAX_PENDING_WARNINGS_PER_RUN = 4


def _normalize_tool_call_args(raw_args: object) -> tuple[dict, str | None]:
    """yyds: 工具调用参数标准化 — 把各种 Provider 返回的 args 格式统一成 dict。

    为什么需要标准化？
      不同 LLM Provider 对 tool_call.args 的序列化方式不一致：
      - OpenAI: 返回 dict（大部分情况走这里，直接返回）
      - 某些 Provider: 返回 JSON 字符串（需要 json.loads 反序列化）
      - 某些 Provider: 返回 None（空参数）
      - 极端情况: 返回其他类型（兜底转字符串）

    返回值: (标准化的 dict, fallback_key)
      - 正常情况: (dict, None) — 参数成功解析为字典
      - 解析失败: ({}, 原始字符串) — fallback_key 用于后续哈希计算
        为什么需要 fallback_key？因为哈希需要确定性输入，解析失败时用原始字符串兜底，
        至少保证同样的错误输入产生同样的哈希。

    执行顺序：
      ① 是 dict → 直接返回（最常见路径，OpenAI 走这里）
      ② 是 str → json.loads 尝试解析 → 成功且是 dict 就返回，失败就用原始字符串做 fallback
      ③ 是 None → 返回空 dict
      ④ 其他类型 → json.dumps 转字符串做 fallback
    """
    if isinstance(raw_args, dict):
        return raw_args, None

    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}, raw_args

        if isinstance(parsed, dict):
            return parsed, None
        return {}, json.dumps(parsed, sort_keys=True, default=str)

    if raw_args is None:
        return {}, None

    return {}, json.dumps(raw_args, sort_keys=True, default=str)


def _stable_tool_key(name: str, args: dict, fallback_key: str | None) -> str:
    """yyds: 为一次工具调用生成稳定的哈希键 — 这是循环检测"第一层"的基础。

    "稳定"是什么意思？同一次工具调用，不管 Provider 怎么序列化，生成的 key 都一样。
    这样才能正确判断"这次调用和上次是否相同"。

    为什么不直接用整个 args 做哈希？因为 args 里有些字段不影响"是否重复"的判断：
      - bash(command="ls", session_id="abc") 和 bash(command="ls", session_id="xyz")
        其实是同一次调用，session_id 不应该影响哈希。
    所以只取"关键字段"（salient_fields）参与哈希。

    三种策略，按工具类型区分（执行顺序）：

      ① read_file → 按行号分桶（200行一桶）
        为什么？Agent 经常连续读同一文件的相邻行（第1-50行、第10-60行），
        参数不完全相同但本质是"在重复读同一个区域"，不应该算两次不同的调用。
        分桶：读第1-50行 → 桶0，读第51-100行 → 桶0（同一个桶），读第201-250行 → 桶1
        返回格式: "path:桶号-桶号"，例如 "/tmp/foo.py:0-0"

      ② write_file / str_replace → 用完整参数哈希
        为什么？同一文件可能被多次更新不同内容（先写版本1，再写版本2），
        这些是真正不同的调用，不应该被合并。所以用完整 args 做哈希。
        如果 normalize 阶段失败（fallback_key 不为 None），直接用 fallback_key。

      ③ 其他工具 → 只取关键字段（path/url/query/command/pattern/glob/cmd）
        忽略不相关字段（如 session_id），避免噪声导致"相同调用"哈希不同。
        如果关键字段都为空，fallback 到完整 args 哈希。

    参数:
      name: 工具名（如 "bash", "read_file", "write_file"）
      args: 标准化后的参数 dict（来自 _normalize_tool_call_args）
      fallback_key: 参数解析失败时的兜底字符串
    """
    if name == "read_file" and fallback_key is None:
        path = args.get("path") or ""
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        bucket_size = 200
        try:
            start_line = int(start_line) if start_line is not None else 1
        except (TypeError, ValueError):
            start_line = 1
        try:
            end_line = int(end_line) if end_line is not None else start_line
        except (TypeError, ValueError):
            end_line = start_line

        start_line, end_line = sorted((start_line, end_line))
        bucket_start = max(start_line, 1)
        bucket_end = max(end_line, 1)
        bucket_start = (bucket_start - 1) // bucket_size
        bucket_end = (bucket_end - 1) // bucket_size
        return f"{path}:{bucket_start}-{bucket_end}"

    if name in {"write_file", "str_replace"}:
        if fallback_key is not None:
            return fallback_key
        return json.dumps(args, sort_keys=True, default=str)

    salient_fields = ("path", "url", "query", "command", "pattern", "glob", "cmd")
    stable_args = {field: args[field] for field in salient_fields if args.get(field) is not None}
    if stable_args:
        return json.dumps(stable_args, sort_keys=True, default=str)

    if fallback_key is not None:
        return fallback_key

    return json.dumps(args, sort_keys=True, default=str)


def _hash_tool_calls(tool_calls: list[dict]) -> str:
    """yyds: 对一组工具调用生成确定性哈希（MD5 前12位）— 循环检测第一层的核心。

    "一组工具调用"是指模型单次响应中发出的所有 tool_calls。
    例如模型一次返回 AIMessage(tool_calls=[bash, read_file])，这就是一组。

    关键设计：与顺序无关。
      [bash(ls), read_file(foo.py)] 和 [read_file(foo.py), bash(ls)] 应该产生相同哈希，
      因为本质上是同一组调用。模型可能每次调整调用顺序，但内容相同。

    执行顺序：
      ① 对每个 tool_call：提取 name 和 args → 标准化 args → 生成 stable_key → 拼成 "name:key"
      ② 对所有 "name:key" 字符串排序（保证与顺序无关）
      ③ 排序后的列表序列化为 JSON → MD5 取前 12 位作为哈希

    举例：
      tool_calls=[{name:"bash", args:{command:"ls"}}, {name:"read_file", args:{path:"/tmp"}}]
      → normalized = ['bash:{"command":"ls"}', 'read_file:/tmp:0-0']
      → sort → ['bash:{"command":"ls"}', 'read_file:/tmp:0-0']
      → json.dumps → '["bash:{\\"command\\":\\"ls\\"}","read_file:/tmp:0-0"]'
      → md5[:12] → "644d05a19cdd"
    """
    normalized: list[str] = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args, fallback_key = _normalize_tool_call_args(tc.get("args", {}))
        key = _stable_tool_key(name, args, fallback_key)

        normalized.append(f"{name}:{key}")

    normalized.sort()
    blob = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


# yyds: 警告消息 — 注入到 AIMessage.content 里，提示模型"你在重复了，停下来"
_WARNING_MSG = "[LOOP DETECTED] You are repeating the same tool calls. Stop calling tools and produce your final answer now. If you cannot complete the task, summarize what you accomplished so far."

# yyds: 工具频率警告消息 — 同一工具被调用太多次（参数不同也算）
_TOOL_FREQ_WARNING_MSG = (
    "[LOOP DETECTED] You have called {tool_name} {count} times without producing a final answer. Stop calling tools and produce your final answer now. If you cannot complete the task, summarize what you accomplished so far."
)

# yyds: 强制停止消息 — 剥离所有 tool_calls，模型被迫输出文本
_HARD_STOP_MSG = "[FORCED STOP] Repeated tool calls exceeded the safety limit. Producing final answer with results collected so far."

# yyds: 工具频率强制停止消息
_TOOL_FREQ_HARD_STOP_MSG = "[FORCED STOP] Tool {tool_name} called {count} times — exceeded the per-tool safety limit. Producing final answer with results collected so far."


class LoopDetectionMiddleware(AgentMiddleware[AgentState]):
    """yyds: 循环检测中间件 — Agent 的"安全刹车"。

    执行时机：after_model（模型返回响应后、工具执行前）
    检测目标：AIMessage 里的 tool_calls 字段

    两层检测：
      第一层（哈希匹配）：同一组工具调用（名称+参数相同）在滑动窗口内重复出现
        - ≥ warn_threshold(3) → 注入 "[LOOP DETECTED]" 警告
        - ≥ hard_limit(5)    → 剥离所有 tool_calls，强制输出文本
      第二层（频率统计）：同一工具不管参数，被调用太多次
        - ≥ tool_freq_warn(30)       → 注入频率警告
        - ≥ tool_freq_hard_limit(50) → 强制停止

    线程安全：所有状态修改都在 threading.Lock() 保护下
    内存控制：OrderedDict + LRU 淘汰，最多追踪 max_tracked_threads 个线程
    """

    def __init__(
        self,
        warn_threshold: int = _DEFAULT_WARN_THRESHOLD,
        hard_limit: int = _DEFAULT_HARD_LIMIT,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        max_tracked_threads: int = _DEFAULT_MAX_TRACKED_THREADS,
        tool_freq_warn: int = _DEFAULT_TOOL_FREQ_WARN,
        tool_freq_hard_limit: int = _DEFAULT_TOOL_FREQ_HARD_LIMIT,
        tool_freq_overrides: dict[str, tuple[int, int]] | None = None,
    ):
        # yyds: 初始化中间件 — 设置阈值 + 创建线程安全的内部状态
        # _history: OrderedDict[thread_id, list[hash]] — 每个线程的工具调用哈希历史
        # _warned: dict[thread_id, set[hash]] — 已经警告过的哈希（每个哈希只警告一次）
        # _tool_freq: dict[thread_id, dict[tool_name, count]] — 每个线程中每个工具的调用次数
        # _tool_freq_warned: dict[thread_id, set[tool_name]] — 已经频率警告过的工具名
        # _lock: threading.Lock — 保护所有内部状态，确保多用户并发安全
        super().__init__()
        self.warn_threshold = warn_threshold
        self.hard_limit = hard_limit
        self.window_size = window_size
        self.max_tracked_threads = max_tracked_threads
        self.tool_freq_warn = tool_freq_warn
        self.tool_freq_hard_limit = tool_freq_hard_limit
        self._tool_freq_overrides: dict[str, tuple[int, int]] = tool_freq_overrides or {}
        self._lock = threading.Lock()
        self._history: OrderedDict[str, list[str]] = OrderedDict()
        self._warned: dict[str, set[str]] = defaultdict(set)
        self._tool_freq: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._tool_freq_warned: dict[str, set[str]] = defaultdict(set)
        # Per-thread/run queue of warnings to inject at the next model call.
        # Populated by ``after_model`` (detection) and drained by
        # ``wrap_model_call`` (injection); see module docstring.
        self._pending_warnings: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._pending_warning_touch_order: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._max_pending_warning_keys = max(1, self.max_tracked_threads * 2)

    @classmethod
    def from_config(cls, config: LoopDetectionConfig) -> LoopDetectionMiddleware:
        """yyds: 从 Pydantic 验证过的配置对象构造中间件（工厂方法）。
        config.yaml 里的值已经过 LoopDetectionConfig 校验（hard_limit ≥ warn_threshold 等），
        这里直接信任并传入，不重复校验。
        """
        return cls(
            warn_threshold=config.warn_threshold,
            hard_limit=config.hard_limit,
            window_size=config.window_size,
            max_tracked_threads=config.max_tracked_threads,
            tool_freq_warn=config.tool_freq_warn,
            tool_freq_hard_limit=config.tool_freq_hard_limit,
            tool_freq_overrides={name: (o.warn, o.hard_limit) for name, o in config.tool_freq_overrides.items()},
        )

    def _get_thread_id(self, runtime: Runtime) -> str:
        """yyds: 从 Runtime 上下文提取 thread_id，用于按线程隔离追踪。
        拿不到就返回 "default"（不应该发生，但防御性编程）。
        """
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id:
            return str(thread_id)
        return "default"

    def _get_run_id(self, runtime: Runtime) -> str:
        """Extract run_id from runtime context for per-run warning scoping."""
        run_id = runtime.context.get("run_id") if runtime.context else None
        if run_id:
            return str(run_id)
        return "default"

    def _pending_key(self, runtime: Runtime) -> tuple[str, str]:
        """Return the pending-warning key for the current thread/run."""
        return self._get_thread_id(runtime), self._get_run_id(runtime)

    def _evict_if_needed(self) -> None:
        """yyds: LRU 淘汰 — 当追踪的线程数超过上限时，踢掉最久未活跃的线程。
        必须在持有 self._lock 的情况下调用。
        踢掉一个线程时，同时清理它在 _history、_warned、_tool_freq、_tool_freq_warned 中的数据。
        """
        while len(self._history) > self.max_tracked_threads:
            evicted_id, _ = self._history.popitem(last=False)
            self._warned.pop(evicted_id, None)
            self._tool_freq.pop(evicted_id, None)
            self._tool_freq_warned.pop(evicted_id, None)
            for key in list(self._pending_warnings):
                if key[0] == evicted_id:
                    self._drop_pending_warning_key_locked(key)
            logger.debug("Evicted loop tracking for thread %s (LRU)", evicted_id)

    def _drop_pending_warning_key_locked(self, key: tuple[str, str]) -> None:
        """Drop all pending-warning bookkeeping for one thread/run key.

        Must be called while holding self._lock.
        """
        self._pending_warnings.pop(key, None)
        self._pending_warning_touch_order.pop(key, None)

    def _touch_pending_warning_key_locked(self, key: tuple[str, str]) -> None:
        """Mark a pending-warning key as recently used.

        Must be called while holding self._lock.
        """
        self._pending_warning_touch_order[key] = None
        self._pending_warning_touch_order.move_to_end(key)

    def _prune_pending_warning_state_locked(self, protected_key: tuple[str, str]) -> None:
        """Cap pending-warning state across abnormal or concurrent runs.

        Must be called while holding self._lock.
        """
        overflow = len(self._pending_warning_touch_order) - self._max_pending_warning_keys
        if overflow <= 0:
            return

        candidates = [key for key in self._pending_warning_touch_order if key != protected_key]
        for key in candidates[:overflow]:
            self._drop_pending_warning_key_locked(key)

    def _queue_pending_warning(self, runtime: Runtime, warning: str) -> None:
        """Queue one transient warning for the current thread/run with caps."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            warnings = self._pending_warnings[pending_key]
            if warning not in warnings:
                warnings.append(warning)
            if len(warnings) > _MAX_PENDING_WARNINGS_PER_RUN:
                del warnings[: len(warnings) - _MAX_PENDING_WARNINGS_PER_RUN]
            self._touch_pending_warning_key_locked(pending_key)
            self._prune_pending_warning_state_locked(protected_key=pending_key)

    def _track_and_check(self, state: AgentState, runtime: Runtime) -> tuple[str | None, bool]:
        """yyds: 核心检测逻辑 — 追踪工具调用并检查是否陷入循环。

        返回值: (警告消息 or None, 是否强制停止)
          - (None, False) → 正常，不干预
          - (警告文本, False) → 注入警告，但允许继续执行工具
          - (强制停止文本, True) → 剥离 tool_calls，强制输出文本

        执行顺序：
          ① 前置检查：消息列表为空 / 最后一条不是 AIMessage / 没有 tool_calls → 直接返回不干预
          ② 生成哈希：对当前 tool_calls 调用 _hash_tool_calls → 得到 call_hash
          ③ 加锁更新历史：把 call_hash 追加到该线程的滑动窗口，超长则裁剪
          ④ 第一层检测（哈希精确匹配）：同一哈希在窗口内出现次数
             - ≥ hard_limit → 返回强制停止
             - ≥ warn_threshold 且没警告过 → 返回警告（每个哈希只警告一次）
          ⑤ 第二层检测（工具频率统计）：遍历本次每个工具，累计计数
             - 某工具 ≥ eff_hard → 返回强制停止
             - 某工具 ≥ eff_warn 且没警告过 → 返回频率警告（每个工具只警告一次）

        为什么第一层优先于第二层？
          第一层是精确匹配（完全相同的调用），说明明确的循环，优先级更高。
          第二层是模糊匹配（参数不同但工具相同），可能是正常的大量使用，优先级较低。
        """
        messages = state.get("messages", [])
        if not messages:
            return None, False

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None, False

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None, False

        # yyds: ① 前置检查通过，② 生成哈希
        thread_id = self._get_thread_id(runtime)
        call_hash = _hash_tool_calls(tool_calls)

        with self._lock:
            # yyds: ③ 加锁更新历史
            # move_to_end：标记该线程为"最近活跃"（LRU 淘汰时不会先踢它）
            if thread_id in self._history:
                self._history.move_to_end(thread_id)
            else:
                self._history[thread_id] = []
                self._evict_if_needed()

            history = self._history[thread_id]
            history.append(call_hash)
            # yyds: 滑动窗口裁剪 — 只保留最近 window_size 次调用的哈希
            # 例如窗口=20，第21次进来后，只保留最后20个
            if len(history) > self.window_size:
                history[:] = history[-self.window_size :]

            warned_hashes = self._warned.get(thread_id)
            if warned_hashes is not None:
                warned_hashes.intersection_update(history)
                if not warned_hashes:
                    self._warned.pop(thread_id, None)

            # yyds: ④ 第一层检测 — 哈希精确匹配
            count = history.count(call_hash)
            tool_names = [tc.get("name", "?") for tc in tool_calls]

            # ④a 达到 hard_limit → 强制停止（剥离 tool_calls）
            if count >= self.hard_limit:
                logger.error(
                    "Loop hard limit reached — forcing stop",
                    extra={
                        "thread_id": thread_id,
                        "call_hash": call_hash,
                        "count": count,
                        "tools": tool_names,
                    },
                )
                return _HARD_STOP_MSG, True

            # ④b 达到 warn_threshold → 注入警告（每个哈希只警告一次，防止重复注入）
            if count >= self.warn_threshold:
                warned = self._warned[thread_id]
                if call_hash not in warned:
                    warned.add(call_hash)
                    logger.warning(
                        "Repetitive tool calls detected — injecting warning",
                        extra={
                            "thread_id": thread_id,
                            "call_hash": call_hash,
                            "count": count,
                            "tools": tool_names,
                        },
                    )
                    return _WARNING_MSG, False

            # yyds: ⑤ 第二层检测 — 工具频率统计
            freq = self._tool_freq[thread_id]
            for tc in tool_calls:
                name = tc.get("name", "")
                if not name:
                    continue
                freq[name] += 1
                tc_count = freq[name]

                # yyds: 查找该工具的自定义阈值（config.yaml 里 tool_freq_overrides）
                # 例如 bash 调用很频繁是正常的，可以设更高阈值
                if name in self._tool_freq_overrides:
                    eff_warn, eff_hard = self._tool_freq_overrides[name]
                else:
                    eff_warn, eff_hard = self.tool_freq_warn, self.tool_freq_hard_limit

                # ⑤a 达到 eff_hard → 强制停止
                if tc_count >= eff_hard:
                    logger.error(
                        "Tool frequency hard limit reached — forcing stop",
                        extra={
                            "thread_id": thread_id,
                            "tool_name": name,
                            "count": tc_count,
                        },
                    )
                    return _TOOL_FREQ_HARD_STOP_MSG.format(tool_name=name, count=tc_count), True

                # ⑤b 达到 eff_warn → 注入频率警告（每个工具只警告一次）
                if tc_count >= eff_warn:
                    warned = self._tool_freq_warned[thread_id]
                    if name not in warned:
                        warned.add(name)
                        logger.warning(
                            "Tool frequency warning — too many calls to same tool type",
                            extra={
                                "thread_id": thread_id,
                                "tool_name": name,
                                "count": tc_count,
                            },
                        )
                        return _TOOL_FREQ_WARNING_MSG.format(tool_name=name, count=tc_count), False

        return None, False

    @staticmethod
    def _append_text(content: str | list | None, text: str) -> str | list:
        """yyds: 安全地往 AIMessage.content 追加警告文本 — 处理三种 content 格式。

        为什么 content 会有三种类型？
          - str: 普通文本（最常见），直接字符串拼接
          - list: Anthropic Claude 的 thinking 模式返回的内容块列表，
            格式是 [{"type":"text","text":"..."}, {"type":"thinking","thinking":"..."}]
            需要追加一个新的 {"type":"text"} 块
          - None: 模型没输出文本内容（只有 tool_calls），直接返回警告文本

        为什么不直接 content += text？
          因为 content 可能是 list 或 None，直接 += 会 TypeError。
          这个方法封装了三种情况的安全处理。
        """
        if content is None:
            return text
        if isinstance(content, list):
            return [*content, {"type": "text", "text": f"\n\n{text}"}]
        if isinstance(content, str):
            return content + f"\n\n{text}"
        return str(content) + f"\n\n{text}"

    @staticmethod
    def _build_hard_stop_update(last_msg, content: str | list) -> dict:
        """yyds: 构建"强制停止"后的 model_copy update 字典 — 把 AIMessage 从"调工具"变成"纯文本回复"。

        这就是之前学过的"替换模式"：用 model_copy(update=...) 保持 id 不变，reducer 原地替换。
        但和 SubagentLimit 不同的是，这里不是截断 tool_calls，而是**彻底清空**。

        三件事：
          ① tool_calls=[] → 阻止工具节点执行（没有 tool_calls 就不会进入工具节点）
          ② additional_kwargs 里删掉 tool_calls 和 function_call → 防止 Provider 残留数据
          ③ response_metadata.finish_reason 从 "tool_calls" 改为 "stop" → 让下游知道调用已结束

        这三步和 clone_ai_message_with_tool_calls 做的事情一样（同步两份存储），
        但这里是"全清空"而非"部分保留"，所以直接手写 update 字典更简洁。
        """
        update = {
            "tool_calls": [],
            "content": content,
        }

        additional_kwargs = dict(getattr(last_msg, "additional_kwargs", {}) or {})
        for key in ("tool_calls", "function_call"):
            additional_kwargs.pop(key, None)
        update["additional_kwargs"] = additional_kwargs

        response_metadata = deepcopy(getattr(last_msg, "response_metadata", {}) or {})
        if response_metadata.get("finish_reason") == "tool_calls":
            response_metadata["finish_reason"] = "stop"
        update["response_metadata"] = response_metadata

        return update

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        """yyds: 中间件的主入口 — 检测循环并决定如何干预。

        执行顺序：
          ① 调用 _track_and_check → 得到 (warning, hard_stop)
          ② 如果 hard_stop=True → 剥离 tool_calls，替换为强制停止消息（模型无法继续调工具）
          ③ 如果有 warning → 保留 tool_calls（工具节点照常执行），但追加警告到 content
          ④ 都没有 → 返回 None（不干预，继续正常流程）

        操作模式：替换模式（model_copy 保持 id → reducer 原地替换）
          ② 和 ③ 都返回 {"messages": [patched_msg]}，patched_msg 和原消息 id 相同。

        临时方案（#2724）：
          警告文本追加到 AIMessage.content 里，而不是注入独立的 HumanMessage。
          为什么？因为在 after_model 阶段，AIMessage(tool_calls) 还没有对应的 ToolMessage，
          如果中间插入一条 HumanMessage，就会破坏 AIMessage → ToolMessage 的配对关系，
          导致 OpenAI/Moonshot 的严格校验报错。
          正确做法应该是在 wrap_model_call 阶段注入（RFC #2517），但还没实现。
        """
        warning, hard_stop = self._track_and_check(state, runtime)

        if hard_stop:
            # yyds: ② 强制停止 — 剥离 tool_calls，追加强制停止文本
            # model_copy + _build_hard_stop_update → id 不变，reducer 替换
            # Strip tool_calls from the last AIMessage to force text output.
            # Once tool_calls are stripped, the AIMessage no longer requires
            # matching ToolMessage responses, so mutating it in place here
            # is safe for OpenAI/Moonshot pairing validators.
            messages = state.get("messages", [])
            last_msg = messages[-1]
            content = self._append_text(last_msg.content, warning or _HARD_STOP_MSG)
            stripped_msg = last_msg.model_copy(update=self._build_hard_stop_update(last_msg, content))
            return {"messages": [stripped_msg]}

        if warning:
            # yyds: ③ 注入警告 — 保留 tool_calls，只改 content
            # model_copy(update={"content": ...}) → id 不变，reducer 替换
            # tool_calls 没被清空 → 工具节点照常执行 → 但 content 里多了警告文本
            # Defer injection to the next model call. We must NOT alter the
            # AIMessage(tool_calls=...) here (would put framework words in
            # the model's mouth, polluting downstream consumers like
            # MemoryMiddleware), nor insert a separate non-tool message
            # (would break OpenAI/Moonshot tool-call pairing because the
            # tools node has not produced ToolMessage responses yet). The
            # warning is delivered via ``wrap_model_call`` below.
            self._queue_pending_warning(runtime, warning)
            return None

        return None

    def _clear_other_run_pending_warnings(self, runtime: Runtime) -> None:
        """Drop stale pending warnings for previous runs in this thread."""
        thread_id, current_run_id = self._pending_key(runtime)
        with self._lock:
            for key in list(self._pending_warnings):
                if key[0] == thread_id and key[1] != current_run_id:
                    self._drop_pending_warning_key_locked(key)

    def _clear_current_run_pending_warnings(self, runtime: Runtime) -> None:
        """Drop pending warnings owned by the current thread/run."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            self._drop_pending_warning_key_locked(pending_key)

    @staticmethod
    def _format_warning_message(warnings: list[str]) -> str:
        """Merge pending warnings into one prompt message."""
        deduped = list(dict.fromkeys(warnings))
        return "\n\n".join(deduped)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_run_pending_warnings(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_run_pending_warnings(runtime)
        return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_current_run_pending_warnings(runtime)
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_current_run_pending_warnings(runtime)
        return None

    def _drain_pending_warnings(self, runtime: Runtime) -> list[str]:
        """Pop and return all queued warnings for *runtime*'s thread/run."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            warnings = self._pending_warnings.pop(pending_key, [])
            self._pending_warning_touch_order.pop(pending_key, None)
        return warnings

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        """Append queued loop warnings (if any) to the outgoing message list.

        The warning is placed *after* every existing message, including the
        ToolMessage responses to the previous AIMessage(tool_calls). This
        keeps ``assistant tool_calls -> tool_messages`` pairing intact for
        OpenAI/Moonshot, avoids the Anthropic mid-stream SystemMessage
        restriction (we use HumanMessage), and never mutates an existing
        AIMessage.
        """
        warnings = self._drain_pending_warnings(request.runtime)
        if not warnings:
            return request
        new_messages = [
            *request.messages,
            HumanMessage(content=self._format_warning_message(warnings), name="loop_warning"),
        ]
        return request.override(messages=new_messages)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))

    def reset(self, thread_id: str | None = None) -> None:
        """yyds: 清理追踪状态。指定 thread_id 只清该线程，不指定则清空全部。
        用于测试和 thread 结束后的清理。
        """
        with self._lock:
            if thread_id:
                self._history.pop(thread_id, None)
                self._warned.pop(thread_id, None)
                self._tool_freq.pop(thread_id, None)
                self._tool_freq_warned.pop(thread_id, None)
                for key in list(self._pending_warnings):
                    if key[0] == thread_id:
                        self._drop_pending_warning_key_locked(key)
            else:
                self._history.clear()
                self._warned.clear()
                self._tool_freq.clear()
                self._tool_freq_warned.clear()
                self._pending_warnings.clear()
                self._pending_warning_touch_order.clear()
