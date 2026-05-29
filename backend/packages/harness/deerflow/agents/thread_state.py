"""yyds: ThreadState — Agent 的"记忆背包"，所有对话数据都在这里。

每次对话，LangGraph 会在各个节点之间传递这个 state。
你可以把它理解为一个 Python dict，Agent 运行过程中不断往里写东西。

字段清单：
  messages       ← 对话历史（从 AgentState 继承的，不用管）
  sandbox        ← 沙箱信息（容器 ID，Agent 执行代码时用的隔离环境）
  thread_data    ← 工作目录（workspace_path / uploads_path / outputs_path）
  title          ← 对话标题（自动生成的，比如"调研 LangGraph"）
  artifacts      ← 展示给用户的文件列表（present_file_tool 写入的）
  todos          ← 待办列表（Pro/Ultra 模式下 Agent 自己管理）
  uploaded_files ← 用户上传的文件
  viewed_images  ← Agent 看过的图片（view_image_tool 写入的）

【大白话讲清楚：reducer 是什么？】
  普通 state 字段：节点返回新值 → 直接覆盖旧值
  带 reducer 的字段：节点返回新值 → 调 reducer 函数合并新旧值

  为什么 artifacts 需要 reducer？
    Agent 第 1 轮展示 report.md → artifacts = ["report.md"]
    Agent 第 3 轮展示 chart.png → artifacts = ["chart.png"]
    如果直接覆盖 → report.md 没了！
    用 merge_artifacts reducer → artifacts = ["report.md", "chart.png"] ✅

  例子：
    旧值：["report.md", "chart.png"]
    新值：["report.md", "summary.md"]
    merge_artifacts 合并后：["report.md", "chart.png", "summary.md"]
    → report.md 不重复（去重），chart.png 和 summary.md 都保留

【大白话讲清楚：Annotated[list[str], merge_artifacts] 是什么意思？】
  这就是 LangGraph 的"字段级合并策略"声明：
    Annotated[字段类型, 合并函数]

  artifacts: Annotated[list[str], merge_artifacts]
    ↑ 类型是 list[str]    ↑ 合并时调 merge_artifacts 函数

  viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]
    ↑ 类型是 dict         ↑ 合并时调 merge_viewed_images 函数

  没有 Annotated 的字段（如 title、todos）→ 新值直接覆盖旧值

为什么用 TypedDict 而不是 Pydantic？
  LangGraph 的状态机制需要"字段级 reducer"（每次节点执行后，
  只合并被修改的字段），TypedDict + Annotated 是 LangGraph 的标准做法。
"""

from typing import Annotated, NotRequired, TypedDict

from langchain.agents import AgentState


class SandboxState(TypedDict):
    """yyds: 沙箱状态 — 记录当前线程使用的沙箱容器 ID。"""

    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    """yyds: 线程工作目录 — 每个对话线程有三个隔离目录。

    三个目录的关系：
      workspace_path — 工作区（Agent 在这里读写文件）
      uploads_path   — 用户上传的文件存放处
      outputs_path   — 产出物目录（present_file_tool 只展示这里的文件）

    比如：
      workspace_path = /app/.deer-flow/threads/abc-123/user-data/workspace
      uploads_path   = /app/.deer-flow/threads/abc-123/user-data/uploads
      outputs_path   = /app/.deer-flow/threads/abc-123/user-data/outputs
    """

    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    """yyds: 一张图片的数据 — base64 编码 + MIME 类型。

    为什么存 base64？
      LLM（如 GPT-4o）理解图片需要 base64 格式。
      view_image_tool 读取图片 → base64 编码 → 存这里 → LLM 就能"看到"图片了。
    """

    base64: str
    mime_type: str


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """yyds: artifacts 合并策略 — 旧列表 + 新列表 → 去重 → 保持顺序。

    三个分支：
      ① 旧值是 None → 返回新值（第一次写入）
      ② 新值是 None → 返回旧值（这次没更新）
      ③ 都有值 → 合并去重，先入的排前面

    去重用 dict.fromkeys() 技巧：
      dict.fromkeys(["a", "b", "a", "c"]) → {"a": None, "b": None, "c": None}
      list(...) → ["a", "b", "c"]
      → 保留了插入顺序，去掉了重复的 "a"

    例子：
      existing = ["/mnt/user-data/outputs/report.md"]
      new = ["/mnt/user-data/outputs/report.md", "/mnt/user-data/outputs/chart.png"]
      → ["/mnt/user-data/outputs/report.md", "/mnt/user-data/outputs/chart.png"]
      → report.md 只出现一次（去重了）

    ---
    Reducer for artifacts list - merges and deduplicates artifacts.
    """
    if existing is None:
        return new or []
    if new is None:
        return existing
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """yyds: viewed_images 合并策略 — 和 artifacts 类似，但有个"清空"机关。

    四个分支：
      ① 旧值是 None → 返回新值（第一次写入）
      ② 新值是 None → 返回旧值（这次没更新）
      ③ 新值是空 dict {} → 清空所有图片（中间件处理完后重置用的）
      ④ 都有值 → 合并（新值覆盖旧值的同名 key）

    为什么要"清空"机关？
      中间件把图片信息注入给 LLM 后，不需要继续保留 base64 数据（太大了）。
      → 中间件返回空 dict {} → merge_viewed_images 返回 {} → 清空了

    例子：
      existing = {"/mnt/outputs/a.png": {base64: "...", mime_type: "image/png"}}
      new = {"/mnt/outputs/b.png": {base64: "...", mime_type: "image/png"}}
      → 两个图片都保留，key 不同不冲突

      new = {}  ← 中间件说"我处理完了，清空吧"
      → 返回 {} ← 清空

    ---
    Reducer for viewed_images dict - merges image dictionaries.
    """
    if existing is None:
        return new or {}
    if new is None:
        return existing
    if len(new) == 0:
        return {}
    return {**existing, **new}


def merge_todos(existing: list | None, new: list | None) -> list | None:
    """Reducer for todos list - keeps the last non-None value.

    Semantics:
    - If `new` is None (node didn't touch todos), preserve `existing`.
    - If `new` is provided (even empty list), it represents an explicit
      update and wins over `existing`.
    """
    if new is None:
        return existing
    return new


class ThreadState(AgentState):
    """yyds: Agent 的完整状态 — LangGraph 图中所有节点共享这一个 dict。

    继承关系：
      AgentState（LangChain 提供）
        └── messages: list[BaseMessage]  ← 对话历史（已有，不用管）
      ThreadState（本文件）
        ├── sandbox         ← 沙箱信息
        ├── thread_data     ← 工作目录
        ├── title           ← 对话标题
        ├── artifacts       ← 展示文件（带 reducer，自动去重合并）
        ├── todos           ← 待办列表
        ├── uploaded_files  ← 上传文件
        └── viewed_images   ← 查看图片（带 reducer，自动合并 + 清空机关）

    NotRequired = 这个字段可能不存在（Optional 的 TypedDict 版本）
    Annotated[type, reducer] = 这个字段用 reducer 函数合并，而不是直接覆盖
    """

    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: Annotated[list | None, merge_todos]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]
