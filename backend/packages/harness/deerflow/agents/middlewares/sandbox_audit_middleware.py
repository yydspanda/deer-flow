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
    # yyds: 高危命令正则列表 — import 时编译一次，运行时 O(n) 逐条匹配
    #   匹配到任意一条 → 返回 "block"，阻止命令执行
    #   每条注释说明了它拦截的攻击类型
    re.compile(r"rm\s+-[^\s]*r[^\s]*\s+(/\*?|~/?\*?|/home\b|/root\b)\s*$"),  # yyds: rm -rf 递归删除根/家目录
    re.compile(r"dd\s+if="),  # yyds: dd 磁盘覆写
    re.compile(r"mkfs"),  # yyds: 格式化文件系统
    re.compile(r"cat\s+/etc/shadow"),  # yyds: 读取影子密码文件
    re.compile(r">+\s*/etc/"),  # yyds: 覆写系统配置
    re.compile(r"\|\s*(ba)?sh\b"),  # yyds: 管道注入 shell（curl|bash、echo|sh 等）
    re.compile(r"[`$]\(?\s*(curl|wget|bash|sh|python|ruby|perl|base64)"),  # yyds: 命令替换注入危险可执行文件
    re.compile(r"base64\s+.*-d.*\|"),  # yyds: base64 解码后管道执行
    re.compile(r">+\s*(/usr/bin/|/bin/|/sbin/)"),  # yyds: 覆写系统二进制
    re.compile(r">+\s*~/?\.(bashrc|profile|zshrc|bash_profile)"),  # yyds: 覆写 shell 启动文件（持久化后门）
    re.compile(r"/proc/[^/]+/environ"),  # yyds: 读取进程环境变量（泄露密钥/Token）
    re.compile(r"\b(LD_PRELOAD|LD_LIBRARY_PATH)\s*="),  # yyds: 动态链接器劫持（一步提权）
    re.compile(r"/dev/tcp/"),  # yyds: bash 内置网络（绕过工具白名单的反向 shell）
    re.compile(r"\S+\(\)\s*\{[^}]*\|\s*\S+\s*&"),  # yyds: fork bomb（:(){ :|:& };:）
    re.compile(r"while\s+true.*&\s*done"),  # yyds: fork bomb 变体（while true; do bash & done）
]

_MEDIUM_RISK_PATTERNS: list[re.Pattern[str]] = [
    # yyds: 中危命令正则列表 — 匹配到 → 返回 "warn"，执行但追加警告
    #   这些命令不直接破坏系统，但可能改变运行环境或引入风险依赖
    re.compile(r"chmod\s+777"),  # yyds: 全权限开放
    re.compile(r"pip3?\s+install"),  # yyds: 安装 Python 包（供应链风险）
    re.compile(r"apt(-get)?\s+install"),  # yyds: 安装系统包
    re.compile(r"\b(sudo|su)\b"),  # yyds: 提权（Docker root 下实际无效果，但仍需警告）
    re.compile(r"\bPATH\s*="),  # yyds: 修改 PATH（可劫持命令，攻击链较长故只警告）
]


def _split_compound_command(command: str) -> list[str]:
    """yyds: 拆分复合命令 — 以 &&、||、; 为分隔符，引号感知。

    例如 "cmd1 && cmd2; cmd3" → ["cmd1", "cmd2", "cmd3"]
    引号内的分隔符不拆分：'echo "a;b"' → 不拆分
    引号未闭合时不拆分（fail-closed），整条命令交给正则检测。

    yyds 执行顺序：
      ① 逐字符扫描，跟踪三种状态：单引号/双引号/转义
      ② 遇到 &&、||（双字符）或 ;（单字符）且不在引号内 → 分割点
      ③ 扫描结束后检查引号/转义是否闭合
         - 未闭合 → fail-closed，返回整条命令不拆分（交给正则匹配，宁可误报不漏报）
         - 正常闭合 → 返回拆分结果
    """
    parts: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    escaping = False
    index = 0

    while index < len(command):
        char = command[index]

        # yyds: 处理转义字符（\x）— 只在双引号外生效
        #   例如 echo hello\\nworld → \n 被当作字面字符
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

        # yyds: ① 跟踪引号状态 — 单引号和双引号独立计数，支持嵌套
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

        # yyds: ② 不在引号内时，检查是否命中分隔符（&&、||、;）
        if not in_single_quote and not in_double_quote:
            if command.startswith("&&", index) or command.startswith("||", index):
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                index += 2  # yyds: && 和 || 是双字符，跳过两个位置
                continue
            if char == ";":
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                index += 1
                continue

        # yyds: 普通字符，直接追加
        current.append(char)
        index += 1

    # yyds: ③ 扫描结束，检查引号/转义是否闭合
    #   fail-closed 策略：未闭合 → 不拆分，返回整条命令
    #   原因：未闭合的引号可能导致拆分出语义不同的子命令，漏过危险模式
    if in_single_quote or in_double_quote or escaping:
        return [command]

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts if parts else [command]


def _classify_single_command(command: str) -> str:
    """yyds: 分类单条命令 — 返回 'block'（高危）、'warn'（中危）或 'pass'（安全）。

    yyds 执行顺序：
      ① 正则匹配原始命令（归一化空白后）→ 命中高危模式 → block
      ② shlex 解析后再正则匹配（防止引号包裹绕过，如 'rm -rf /'）
         - 解析失败（引号未闭合）→ block（fail-closed）
      ③ 正则匹配中危模式 → warn
      ④ 都没命中 → pass

    为什么要双保险（①+②）？
      攻击者可能用引号包裹绕过正则：'rm -rf /' 直接匹配不到 r"rm\\s+-.*r"，
      但 shlex.split('rm -rf /') → ['rm', '-rf', '/']，join 后 "rm -rf /" 就能匹配。
    """
    normalized = " ".join(command.split())

    # yyds: ① 正则匹配原始命令 → 高危
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return "block"

    # yyds: ② shlex 解析后再正则匹配 → 高危（防止引号包裹绕过）
    try:
        tokens = shlex.split(command)
        joined = " ".join(tokens)
        for pattern in _HIGH_RISK_PATTERNS:
            if pattern.search(joined):
                return "block"
    except ValueError:
        # yyds: shlex.split 失败（引号未闭合等）→ 视为可疑，直接 block
        return "block"

    # yyds: ③ 正则匹配 → 中危
    for pattern in _MEDIUM_RISK_PATTERNS:
        if pattern.search(normalized):
            return "warn"

    # yyds: ④ 都没命中 → 安全
    return "pass"


def _classify_command(command: str) -> str:
    """yyds: 分类完整命令（可能含复合语句）— 两遍扫描策略。

    yyds 执行顺序：
      ① 第一遍：整条命令直接匹配高危模式
         为什么不先拆分？因为某些攻击模式跨越多条语句，拆分会丢失上下文。
         例如 fork bomb ":(){ :|:& };:" 中函数定义和调用在不同"子命令"里，
         拆开后单条看都不危险，但整体是 fork bomb。
      ② 第二遍：拆分复合命令（&&、||、;），逐条调用 _classify_single_command 分类
         取最严重结果：block > warn > pass。发现 block 立即短路返回。

    举例：
      "cd /workspace && rm -rf /"
      ① 整条匹配高危模式 → 未命中（"cd /workspace && rm -rf /" 不直接匹配 rm -rf 模式）
      ② 拆分为 ["cd /workspace", "rm -rf /"]
         - "cd /workspace" → pass
         - "rm -rf /" → block ← 最严重
         → 返回 "block"
    """
    # yyds: ① 第一遍 — 整条命令匹配高危模式（捕获跨语句攻击）
    normalized = " ".join(command.split())
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return "block"

    # yyds: ② 第二遍 — 拆分后逐条分类，取最严重结果
    sub_commands = _split_compound_command(command)
    worst = "pass"
    for sub in sub_commands:
        verdict = _classify_single_command(sub)
        if verdict == "block":
            return "block"  # yyds: 短路 — block 已经是最严重，不用继续了
        if verdict == "warn":
            worst = "warn"
    return worst


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SandboxAuditMiddleware(AgentMiddleware[ThreadState]):
    """yyds: 沙箱审计中间件 — bash 命令的安全门卫。

    执行时机：wrap_tool_call（包裹 bash 工具的调用）
    操作模式：wrap_tool_call（精确拦截）— 和 DanglingToolCall 的 wrap_model_call 不同，
      这里拦截的是"工具调用"而非"模型调用"。在命令实际执行前拦截。

    只拦截 name="bash" 的工具调用，其他工具直接放行。

    三级分类：
      block（高危）：rm -rf /、curl|bash、dd、mkfs、fork bomb 等 → 不调用 handler，直接返回错误 ToolMessage
      warn（中危）：pip install、sudo、chmod 777 等 → 调用 handler 执行，但追加警告到结果
      pass（安全）：正常执行，原样返回 handler 结果

    审计日志：每次 bash 调用都记录结构化 JSON（时间戳、线程、命令、分类）。

    数据流：
      bash 工具调用 → _pre_process()
                       ├─ _validate_input()  → 拒绝空/null/超长
                       ├─ _classify_command() → 两遍扫描分类
                       └─ _write_audit()      → 结构化日志
      然后：
        block → _build_block_message()（不执行 handler）
        warn  → handler() + _append_warn_to_result()（执行但追加警告）
        pass  → handler()（正常执行）
    """

    state_schema = ThreadState

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_thread_id(self, request: ToolCallRequest) -> str | None:
        """yyds: 从 ToolCallRequest 的 runtime 上下文提取 thread_id。

        yyds 执行顺序：
          ① 先从 runtime.context["thread_id"] 取（主要路径）
          ② 取不到就从 runtime.config["configurable"]["thread_id"] 取（备用路径）
          ③ 都取不到 → 返回 None（审计日志记录为 "unknown"）
        """
        runtime = request.runtime
        if runtime is None:
            return None
        # yyds: ① 主路径 — runtime.context
        ctx = getattr(runtime, "context", None) or {}
        thread_id = ctx.get("thread_id") if isinstance(ctx, dict) else None
        if thread_id is None:
            # yyds: ② 备用路径 — runtime.config.configurable
            cfg = getattr(runtime, "config", None) or {}
            thread_id = cfg.get("configurable", {}).get("thread_id")
        return thread_id

    _AUDIT_COMMAND_LIMIT = 200  # yyds: 审计日志命令截断长度，防止超长命令撑爆日志

    def _write_audit(self, thread_id: str | None, command: str, verdict: str, *, truncate: bool = False) -> None:
        """yyds: 写结构化审计日志 — JSON 格式，写入 langgraph.log。

        yyds 执行顺序：
          ① 如果 truncate=True 且命令超长 → 截断到 200 字符 + 后缀 (... N chars)
          ② 构建结构化记录：{timestamp, thread_id, command, verdict}
          ③ logger.info 写入（会被 langgraph.log 的 JSON formatter 收集）

        为什么超长命令要截断？
          10000 字符的命令如果完整写入日志，单条日志就几十 KB，
          高频攻击会撑爆磁盘。200 字符足够定位问题。
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
        """yyds: 构建 block 时的错误 ToolMessage — 让 Agent 知道命令被拦截了。

        关键设计：status="error"，让模型把这条 ToolMessage 当作工具执行失败处理，
        不会认为命令成功了。模型会尝试其他方法。
        """
        tool_call_id = str(request.tool_call.get("id") or "missing_id")
        return ToolMessage(
            content=f"Command blocked: {reason}. Please use a safer alternative approach.",
            tool_call_id=tool_call_id,
            name="bash",
            status="error",
        )

    def _append_warn_to_result(self, result: ToolMessage | Command, command: str) -> ToolMessage | Command:
        """yyds: 往工具执行结果追加警告文本 — 中危命令执行了但模型需要知道风险。

        yyds 执行顺序：
          ① 如果 result 不是 ToolMessage（可能是 Command）→ 不处理，原样返回
          ② 根据 content 类型追加警告：
             - list（Anthropic thinking 模式）→ 追加 {"type":"text"} 块
             - str（普通模式）→ 字符串拼接
          ③ 返回新 ToolMessage（保持 tool_call_id/name/status 不变）

        为什么不改原 result 而是 new ToolMessage？
          ToolMessage 是不可变的（或语义上不应修改），所以创建新实例。
        """
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
    _MAX_COMMAND_LENGTH = 10_000  # yyds: 正常 bash 命令很少超过几百字符，10000 是远超合法用例的上限

    def _validate_input(self, command: str) -> str | None:
        """yyds: 输入清洗 — 在正则分析之前拦截格式异常的输入。

        yyds 执行顺序：
          ① 空命令（纯空白）→ 拒绝 "empty command"
          ② 超长命令（>10000字符）→ 拒绝 "command too long"
             为什么？正常命令不会这么长，超长几乎一定是 base64 编码的攻击载荷
          ③ 含 null 字节（\\x00）→ 拒绝 "null byte detected"
             为什么？null 字节可用于截断字符串绕过检测，如 "ls\\x00; rm -rf /"

        返回值：None = 通过，str = 拒绝原因
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
        """yyds: 预处理 — 从 ToolCallRequest 提取命令，清洗+分类+写审计日志。

        yyds 执行顺序：
          ① 从 request.tool_call["args"]["command"] 提取命令字符串
             - None 或非字符串 → 当作空字符串处理（后续 _validate_input 会拦截）
          ② 输入清洗 _validate_input → 不通过则直接 block，写审计日志（truncate=True）
          ③ 命令分类 _classify_command → 两遍扫描，得到 verdict
          ④ 写审计日志（正常命令不截断）
          ⑤ block/warn 级别额外写 logger.warning

        返回 (command, thread_id, verdict, reject_reason)：
          - reject_reason 非 None → 输入清洗阶段被拒绝
          - reject_reason 为 None → 正常分类结果在 verdict 里
        """
        # yyds: ① 提取命令
        args = request.tool_call.get("args", {})
        raw_command = args.get("command")
        command = raw_command if isinstance(raw_command, str) else ""
        thread_id = self._get_thread_id(request)

        # yyds: ② 输入清洗 — 拒绝空/null/超长，不通过直接 block
        reject_reason = self._validate_input(command)
        if reject_reason:
            self._write_audit(thread_id, command, "block", truncate=True)
            logger.warning("[SandboxAudit] INVALID INPUT thread=%s reason=%s", thread_id, reject_reason)
            return command, thread_id, "block", reject_reason

        # yyds: ③ 命令分类 — 两遍扫描（整条+拆分）
        verdict = _classify_command(command)

        # yyds: ④ 写审计日志
        self._write_audit(thread_id, command, verdict)

        # yyds: ⑤ 额外 warning 日志
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
        """yyds: 同步版 — 只拦截 bash 工具，block 直接返回错误，warn 追加警告，pass 放行。

        yyds 执行顺序：
          ① 非 bash 工具 → 直接调用 handler，不拦截
          ② 调用 _pre_process 提取+清洗+分类
          ③ block → 不调用 handler，直接返回 _build_block_message 错误 ToolMessage
          ④ warn → 调用 handler 执行命令，然后 _append_warn_to_result 追加警告
          ⑤ pass → 调用 handler 执行命令，原样返回结果
        """
        # yyds: ① 非 bash 工具直接放行
        if request.tool_call.get("name") != "bash":
            return handler(request)

        # yyds: ② 预处理（清洗+分类+审计）
        command, _, verdict, reject_reason = self._pre_process(request)
        # yyds: ③ block → 不执行，返回错误
        if verdict == "block":
            reason = reject_reason or "security violation detected"
            return self._build_block_message(request, reason)
        # yyds: ④⑤ 执行 handler
        result = handler(request)
        # yyds: ④ warn → 追加警告到结果
        if verdict == "warn":
            result = self._append_warn_to_result(result, command)
        # yyds: ⑤ pass → 原样返回
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """yyds: 异步版 — 逻辑和 wrap_tool_call 完全相同，只是 handler 是 await 的。"""
        # yyds: ① 非 bash 工具直接放行
        if request.tool_call.get("name") != "bash":
            return await handler(request)

        # yyds: ② 预处理
        command, _, verdict, reject_reason = self._pre_process(request)
        # yyds: ③ block
        if verdict == "block":
            reason = reject_reason or "security violation detected"
            return self._build_block_message(request, reason)
        # yyds: ④⑤ 执行 handler（异步）
        result = await handler(request)
        # yyds: ④ warn
        if verdict == "warn":
            result = self._append_warn_to_result(result, command)
        return result
