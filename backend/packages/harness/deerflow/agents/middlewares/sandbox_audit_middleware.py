"""yyds: 沙箱审计中间件 — 对 bash 工具执行的安全审计和命令拦截。

【做什么】拦截所有 bash 工具调用，对 shell 命令进行安全分类（block/warn/pass），
   阻止高危命令执行，对中危命令追加警告，并记录所有 bash 调用的结构化审计日志。
【为什么存在】Agent 拥有执行 shell 命令的能力，存在安全风险。如果模型被诱导执行
   "rm -rf /" 或 "curl ... | bash" 等破坏性命令，会造成严重后果。此中间件是安全防线。
【在链中的位置】wrap_tool_call 阶段执行，包裹 bash 工具的调用过程，在命令实际执行前拦截。
【关键设计】
   - 命令分类策略：
     - 高危（block）：rm -rf /、curl|bash、dd if=、mkfs、fork bomb、LD_PRELOAD、/dev/tcp 等，
       直接阻止执行，返回错误 ToolMessage。
     - 中危（warn）：pip install、apt install、chmod 777、sudo/su、PATH= 等，
       正常执行但在结果中追加警告文本，提醒模型注意。
     - 安全（pass）：正常执行。
   - 输入清洗：拒绝空命令、超长命令（>10000字符）、包含 null 字节的命令。
   - 支持复合命令拆分（以 &&、||、; 分隔），对每个子命令独立分类，取最严重结果。
   - 使用 shlex 解析 + 正则匹配双保险，即使引号未闭合也能安全处理。
   - 审计日志为结构化 JSON，包含时间戳、线程ID、命令内容、分类结果，写入 langgraph.log。
   - 同时覆盖同步（wrap_tool_call）和异步（awrap_tool_call）两条调用路径。
"""

import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command classification rules
# ---------------------------------------------------------------------------

# Each pattern is compiled once at import time.
_HIGH_RISK_PATTERNS: list[re.Pattern[str]] = [
    # --- original rules (retained) ---
    re.compile(r"rm\s+-[^\s]*r[^\s]*\s+(/\*?|~/?\*?|/home\b|/root\b)\s*$"),
    re.compile(r"dd\s+if="),
    re.compile(r"mkfs"),
    re.compile(r"cat\s+/etc/shadow"),
    re.compile(r">+\s*/etc/"),
    # --- pipe to sh/bash (generalised, replaces old curl|sh rule) ---
    re.compile(r"\|\s*(ba)?sh\b"),
    # --- command substitution (targeted – only dangerous executables) ---
    re.compile(r"[`$]\(?\s*(curl|wget|bash|sh|python|ruby|perl|base64)"),
    # --- base64 decode piped to execution ---
    re.compile(r"base64\s+.*-d.*\|"),
    # --- overwrite system binaries ---
    re.compile(r">+\s*(/usr/bin/|/bin/|/sbin/)"),
    # --- overwrite shell startup files ---
    re.compile(r">+\s*~/?\.(bashrc|profile|zshrc|bash_profile)"),
    # --- process environment leakage ---
    re.compile(r"/proc/[^/]+/environ"),
    # --- dynamic linker hijack (one-step escalation) ---
    re.compile(r"\b(LD_PRELOAD|LD_LIBRARY_PATH)\s*="),
    # --- bash built-in networking (bypasses tool allowlists) ---
    re.compile(r"/dev/tcp/"),
    # --- fork bomb ---
    re.compile(r"\S+\(\)\s*\{[^}]*\|\s*\S+\s*&"),  # :(){ :|:& };:
    re.compile(r"while\s+true.*&\s*done"),  # while true; do bash & done
]

_MEDIUM_RISK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"chmod\s+777"),
    re.compile(r"pip3?\s+install"),
    re.compile(r"apt(-get)?\s+install"),
    # sudo/su: no-op under Docker root; warn so LLM is aware
    re.compile(r"\b(sudo|su)\b"),
    # PATH modification: long attack chain, warn rather than block
    re.compile(r"\bPATH\s*="),
]


def _split_compound_command(command: str) -> list[str]:
    """yyds: 拆分复合命令 — 以 &&、||、; 为分隔符，引号感知。

    例如 "cmd1 && cmd2; cmd3" → ["cmd1", "cmd2", "cmd3"]
    引号内的分隔符不拆分：'echo "a;b"' → 不拆分
    引号未闭合时不拆分（fail-closed），整条命令交给正则检测。
    """
    parts: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    escaping = False
    index = 0

    while index < len(command):
        char = command[index]

        if escaping:
            current.append(char)
            escaping = False
            index += 1
            continue

        if char == "\\" and not in_single_quote:
            current.append(char)
            escaping = True
            index += 1
            continue

        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(char)
            index += 1
            continue

        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(char)
            index += 1
            continue

        if not in_single_quote and not in_double_quote:
            if command.startswith("&&", index) or command.startswith("||", index):
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                index += 2
                continue
            if char == ";":
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                index += 1
                continue

        current.append(char)
        index += 1

    # Unclosed quote or dangling escape → fail-closed, return whole command
    if in_single_quote or in_double_quote or escaping:
        return [command]

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts if parts else [command]


def _classify_single_command(command: str) -> str:
    """yyds: 分类单条命令 — 返回 'block'（高危）、'warn'（中危）或 'pass'（安全）。

    双重检测：
      1. 正则匹配原始命令
      2. shlex 解析后再正则匹配（防止引号包裹绕过）
    shlex 解析失败（如引号未闭合）→ 视为高危（block），保守策略。
    """
    normalized = " ".join(command.split())

    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return "block"

    # Also try shlex-parsed tokens for high-risk detection
    try:
        tokens = shlex.split(command)
        joined = " ".join(tokens)
        for pattern in _HIGH_RISK_PATTERNS:
            if pattern.search(joined):
                return "block"
    except ValueError:
        # shlex.split fails on unclosed quotes — treat as suspicious
        return "block"

    for pattern in _MEDIUM_RISK_PATTERNS:
        if pattern.search(normalized):
            return "warn"

    return "pass"


def _classify_command(command: str) -> str:
    """yyds: 分类完整命令（可能含复合语句）— 两遍扫描策略。

    第一遍：整条命令匹配高危模式（如 fork bomb 跨越多条语句，拆分会丢失上下文）
    第二遍：拆分复合命令，逐条分类，取最严重结果
    """
    # Pass 1: whole-command high-risk scan (catches multi-statement patterns)
    normalized = " ".join(command.split())
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return "block"

    # Pass 2: per-sub-command classification
    sub_commands = _split_compound_command(command)
    worst = "pass"
    for sub in sub_commands:
        verdict = _classify_single_command(sub)
        if verdict == "block":
            return "block"  # short-circuit: can't get worse
        if verdict == "warn":
            worst = "warn"
    return worst


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SandboxAuditMiddleware(AgentMiddleware[ThreadState]):
    """yyds: 沙箱审计中间件 — bash 命令的安全门卫。

    执行时机：wrap_tool_call（包裹 bash 工具的调用）
    只拦截 name="bash" 的工具调用，其他工具直接放行。

    三级分类：
      block（高危）：rm -rf /、curl|bash、dd、mkfs、fork bomb 等 → 阻止执行
      warn（中危）：pip install、sudo、chmod 777 等 → 执行但追加警告
      pass（安全）：正常执行

    审计日志：每次 bash 调用都记录结构化 JSON（时间戳、线程、命令、分类）。
    """

    state_schema = ThreadState

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_thread_id(self, request: ToolCallRequest) -> str | None:
        """yyds: 从 ToolCallRequest 的 runtime 上下文提取 thread_id。"""
        runtime = request.runtime
        if runtime is None:
            return None
        ctx = getattr(runtime, "context", None) or {}
        thread_id = ctx.get("thread_id") if isinstance(ctx, dict) else None
        if thread_id is None:
            cfg = getattr(runtime, "config", None) or {}
            thread_id = cfg.get("configurable", {}).get("thread_id")
        return thread_id

    _AUDIT_COMMAND_LIMIT = 200

    def _write_audit(self, thread_id: str | None, command: str, verdict: str, *, truncate: bool = False) -> None:
        """yyds: 写结构化审计日志 — JSON 格式，包含时间戳/线程ID/命令/分类结果。
        命令超长时截断到 200 字符（防止日志爆炸）。
        """
        audited_command = command
        if truncate and len(command) > self._AUDIT_COMMAND_LIMIT:
            audited_command = f"{command[: self._AUDIT_COMMAND_LIMIT]}... ({len(command)} chars)"
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "thread_id": thread_id or "unknown",
            "command": audited_command,
            "verdict": verdict,
        }
        logger.info("[SandboxAudit] %s", json.dumps(record, ensure_ascii=False))

    def _build_block_message(self, request: ToolCallRequest, reason: str) -> ToolMessage:
        """yyds: 构建阻止执行时的错误 ToolMessage — 让 Agent 知道命令被拦截了。"""
        tool_call_id = str(request.tool_call.get("id") or "missing_id")
        return ToolMessage(
            content=f"Command blocked: {reason}. Please use a safer alternative approach.",
            tool_call_id=tool_call_id,
            name="bash",
            status="error",
        )

    def _append_warn_to_result(self, result: ToolMessage | Command, command: str) -> ToolMessage | Command:
        """yyds: 往工具执行结果追加警告文本 — 中危命令执行了但模型需要知道风险。"""
        if not isinstance(result, ToolMessage):
            return result
        warning = f"\n\n⚠️ Warning: `{command}` is a medium-risk command that may modify the runtime environment."
        if isinstance(result.content, list):
            new_content = list(result.content) + [{"type": "text", "text": warning}]
        else:
            new_content = str(result.content) + warning
        return ToolMessage(
            content=new_content,
            tool_call_id=result.tool_call_id,
            name=result.name,
            status=result.status,
        )

    # ------------------------------------------------------------------
    # Input sanitisation
    # ------------------------------------------------------------------

    # Normal bash commands rarely exceed a few hundred characters.  10 000 is
    # well above any legitimate use case yet a tiny fraction of Linux ARG_MAX.
    # Anything longer is almost certainly a payload injection or base64-encoded
    # attack string.
    _MAX_COMMAND_LENGTH = 10_000

    def _validate_input(self, command: str) -> str | None:
        """yyds: 输入清洗 — 拒绝空命令、超长命令（>10000字符）、含 null 字节的命令。
        返回 None 表示通过，字符串表示拒绝原因。
        """
        if not command.strip():
            return "empty command"
        if len(command) > self._MAX_COMMAND_LENGTH:
            return "command too long"
        if "\x00" in command:
            return "null byte detected"
        return None

    # ------------------------------------------------------------------
    # Core logic (shared between sync and async paths)
    # ------------------------------------------------------------------

    def _pre_process(self, request: ToolCallRequest) -> tuple[str, str | None, str, str | None]:
        """yyds: 预处理 — 提取命令 → 输入清洗 → 命令分类 → 写审计日志。
        返回 (command, thread_id, verdict, reject_reason)。
        """
        args = request.tool_call.get("args", {})
        raw_command = args.get("command")
        command = raw_command if isinstance(raw_command, str) else ""
        thread_id = self._get_thread_id(request)

        # ① input sanitisation — reject malformed input before regex analysis
        reject_reason = self._validate_input(command)
        if reject_reason:
            self._write_audit(thread_id, command, "block", truncate=True)
            logger.warning("[SandboxAudit] INVALID INPUT thread=%s reason=%s", thread_id, reject_reason)
            return command, thread_id, "block", reject_reason

        # ② classify command
        verdict = _classify_command(command)

        # ③ audit log
        self._write_audit(thread_id, command, verdict)

        if verdict == "block":
            logger.warning("[SandboxAudit] BLOCKED thread=%s cmd=%r", thread_id, command)
        elif verdict == "warn":
            logger.warning("[SandboxAudit] WARN (medium-risk) thread=%s cmd=%r", thread_id, command)

        return command, thread_id, verdict, None

    # ------------------------------------------------------------------
    # wrap_tool_call hooks
    # ------------------------------------------------------------------

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """yyds: 同步版 — 只拦截 bash 工具，block 直接返回错误，warn 追加警告，pass 放行。"""
        if request.tool_call.get("name") != "bash":
            return handler(request)

        command, _, verdict, reject_reason = self._pre_process(request)
        if verdict == "block":
            reason = reject_reason or "security violation detected"
            return self._build_block_message(request, reason)
        result = handler(request)
        if verdict == "warn":
            result = self._append_warn_to_result(result, command)
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """yyds: 异步版 — 同上逻辑。"""
        if request.tool_call.get("name") != "bash":
            return await handler(request)

        command, _, verdict, reject_reason = self._pre_process(request)
        if verdict == "block":
            reason = reject_reason or "security violation detected"
            return self._build_block_message(request, reason)
        result = await handler(request)
        if verdict == "warn":
            result = self._append_warn_to_result(result, command)
        return result
