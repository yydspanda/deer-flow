"""yyds: 文件展示工具 — Agent 创建的文件怎么让用户看到？靠这个。

【大白话讲清楚】
  Agent 写了一个报告（report.md），但用户看不到——因为文件在沙箱里。
  这个工具就是把文件路径"注册"到 state["artifacts"] 里，
  前端读到 artifacts 列表后，会在 UI 上渲染出来（PDF、图片、代码高亮等）。

  安全限制：只允许展示 /mnt/user-data/outputs/ 下的文件。
  → Agent 不能把 /etc/passwd 这种敏感路径展示给用户。

【具体例子】
  Agent 写了一个报告：
    write_file("/mnt/user-data/outputs/report.md", "# 调研结果\\n...")

  然后调用展示：
    present_files(["/mnt/user-data/outputs/report.md"])

  正常流程：
    → 路径标准化为 "/mnt/user-data/outputs/report.md"
    → 写入 state["artifacts"] = ["/mnt/user-data/outputs/report.md"]
    → 前端读取 artifacts，渲染报告 ✅

  异常流程（路径不合法）：
    present_files(["/etc/passwd"])
    → _normalize_presented_filepath 检测到不在 outputs 目录下
    → 返回 ToolMessage("Error: Only files in /mnt/user-data/outputs can be presented")

  异常流程（虚拟路径 vs 宿主机路径）：
    present_files(["/mnt/user-data/outputs/report.md"])        ← 虚拟路径 ✅
    present_files(["/app/backend/.deer-flow/threads/xxx/..."])  ← 宿主机路径，也能处理 ✅
    两种格式都能接受，内部统一转成虚拟路径

【在链中的位置】
  调用者：Agent（LLM 决定调用）→ present_files → state["artifacts"]
  消费者：前端 UI 读取 state["artifacts"] → 渲染文件
  注册位置：tools.py 的 BUILTIN_TOOLS（始终加载）
  state 更新：ThreadState 的 merge_artifacts reducer 自动去重合并

---
Make files visible to the user for viewing and rendering in the client interface.
"""

from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.types import Command

from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.tools.types import Runtime

OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"  # yyds: /mnt/user-data/outputs — 展示文件的唯一合法目录


def _get_thread_id(runtime: Runtime) -> str | None:
    """yyds: 从三个地方找 thread_id（线程唯一标识）。

    为什么需要三个地方？
      runtime.context["thread_id"] — 大部分情况在这里
      runtime.config["configurable"]["thread_id"] — 某些调用路径在这里
      get_config()["configurable"]["thread_id"] — LangGraph 原生上下文

    三个地方按优先级依次尝试，找到就返回。

    例子：
      runtime.context = {"thread_id": "abc-123"} → 直接返回 "abc-123"
      runtime.context = None → 去 config 找 → 找到 → 返回
      都没有 → 返回 None（后续会报错）
    """
    # ① 从 runtime.context 取（最常见）
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id:
        return thread_id

    # ② 从 runtime.config["configurable"] 取
    runtime_config = getattr(runtime, "config", None) or {}
    thread_id = runtime_config.get("configurable", {}).get("thread_id")
    if thread_id:
        return thread_id

    # ③ 从 LangGraph 原生上下文取（兜底）
    try:
        return get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:
        return None


def _normalize_presented_filepath(
    runtime: Runtime,
    filepath: str,
) -> str:
    """yyds: 路径标准化 — 不管 Agent 传虚拟路径还是宿主机路径，都转成统一格式。

    接受两种输入：
      虚拟路径：/mnt/user-data/outputs/report.md
        → 直接解析成宿主机实际路径
      宿主机路径：/app/backend/.deer-flow/threads/<thread>/user-data/outputs/report.md
        → 直接用

    两种路径最终都要验证：是不是在 outputs 目录下？
    不在 → 报错："Only files in /mnt/user-data/outputs can be presented"

    安全为什么重要：
      如果不限制路径，Agent 可以把 /etc/shadow 展示给用户，
      或者把其他线程的文件展示给当前用户（跨线程泄露）。

    执行步骤：
      ① 检查 runtime.state 存在
      ② 获取 thread_id
      ③ 获取当前线程的 outputs 目录实际路径
      ④ 判断传入的是虚拟路径还是宿主机路径 → 转成实际路径
      ⑤ 验证实际路径在 outputs 目录下
      ⑥ 返回标准化的虚拟路径 "/mnt/user-data/outputs/xxx"

    ---
    Normalize a presented file path to the `/mnt/user-data/outputs/*` contract.
    """
    # ① 没有 state → 没法获取线程信息
    if runtime.state is None:
        raise ValueError("Thread runtime state is not available")

    # ② 获取 thread_id（哪个对话线程）
    thread_id = _get_thread_id(runtime)
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context or runtime config")

    # ③ 获取当前线程的 outputs 目录（宿主机上的实际路径）
    thread_data = runtime.state.get("thread_data") or {}
    outputs_path = thread_data.get("outputs_path")
    if not outputs_path:
        raise ValueError("Thread outputs path is not available in runtime state")

    # ④ 判断传入的是虚拟路径还是宿主机路径
    outputs_dir = Path(outputs_path).resolve()
    stripped = filepath.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

    if stripped == virtual_prefix or stripped.startswith(virtual_prefix + "/"):
        # 虚拟路径 → 解析成宿主机实际路径
        # 比如 /mnt/user-data/outputs/report.md → /app/.deer-flow/threads/xxx/outputs/report.md
        try:
            actual_path = get_paths().resolve_virtual_path(thread_id, filepath, user_id=get_effective_user_id())
        except TypeError:
            actual_path = get_paths().resolve_virtual_path(thread_id, filepath)
    else:
        # 宿主机路径 → 直接用
        actual_path = Path(filepath).expanduser().resolve()

    # ⑤ 验证：实际路径必须在 outputs 目录下
    #    如果不在 → 说明 Agent 试图展示 outputs 以外的文件 → 拒绝
    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"Only files in {OUTPUTS_VIRTUAL_PREFIX} can be presented: {filepath}") from exc

    # ⑥ 返回标准化的虚拟路径
    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative_path.as_posix()}"


@tool("present_files", parse_docstring=True)
def present_file_tool(
    runtime: Runtime,
    filepaths: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """yyds: 把文件展示给用户 — 写入 state["artifacts"]，前端自动渲染。

    什么时候用：
      Agent 写了一个报告/生了一张图/创建了任何用户需要看到的文件 → 调这个

    什么时候不用：
      Agent 只是内部读文件做处理，不需要给用户看 → 不调

    安全限制：只能展示 /mnt/user-data/outputs/ 下的文件。

    参数：
      runtime: 注入的运行时上下文（LangGraph 自动注入，Agent 不需要传）
      filepaths: 要展示的文件路径列表（可以一次展示多个）
      tool_call_id: 工具调用 ID（LangGraph 自动注入）

    返回：
      Command(update={"artifacts": [路径列表], "messages": [ToolMessage]})
      → merge_artifacts reducer 自动合并去重
      → 前端读 artifacts 列表渲染

    例子：
      Agent 调用 present_files(["/mnt/user-data/outputs/report.md"])
      → state["artifacts"] = ["/mnt/user-data/outputs/report.md"]
      → 前端渲染报告 ✅

    ---
    Make files visible to the user for viewing and rendering in the client interface.

    When to use the present_files tool:

    - Making any file available for the user to view, download, or interact with
    - Presenting multiple related files at once
    - After creating files that should be presented to the user

    When NOT to use the present_files tool:
    - When you only need to read file contents for your own processing
    - For temporary or intermediate files not meant for user viewing

    Notes:
    - You should call this tool after creating files and moving them to the `/mnt/user-data/outputs` directory.
    - This tool can be safely called in parallel with other tools. State updates are handled by a reducer to prevent conflicts.

    Args:
        filepaths: List of absolute file paths to present to the user. **Only** files in `/mnt/user-data/outputs` can be presented.
    """
    # ① 标准化所有路径（虚拟路径→统一格式，非法路径→报错）
    try:
        normalized_paths = [_normalize_presented_filepath(runtime, filepath) for filepath in filepaths]
    except ValueError as exc:
        return Command(
            update={"messages": [ToolMessage(f"Error: {exc}", tool_call_id=tool_call_id)]},
        )

    # ② 写入 state["artifacts"] — merge_artifacts reducer 会自动合并去重
    #    不会覆盖之前展示的文件，而是追加
    return Command(
        update={
            "artifacts": normalized_paths,
            "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
        },
    )
