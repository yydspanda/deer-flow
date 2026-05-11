"""
yyds: ThreadState — Agent 运行时的"记忆背包"

每次对话，Agent 都带着一个 ThreadState 在各个节点之间流转。
你可以把它理解为"对话过程中所有需要记住的东西"：

  messages      ← 对话历史（AgentState 继承来的）
  sandbox       ← 沙箱容器 ID（Agent 执行代码时的隔离环境）
  thread_data   ← 工作目录路径（上传/工作/输出）
  title         ← 对话标题（自动生成）
  artifacts     ← 产出物列表（文件路径，只增不删，自动去重）
  todos         ← 待办列表（Pro/Ultra 模式下 Agent 自己管理）
  uploaded_files← 用户上传的文件
  viewed_images ← Agent 看过的图片（base64 + MIME 类型，自动合并）

关键字段用 Annotated[type, reducer] 标注了"合并策略"：
  - artifacts:     merge_artifacts — 旧 + 新合并后去重
  - viewed_images: merge_viewed_images — 旧 + 新合并，空 dict 清空

为什么用 TypedDict 而不是 Pydantic？
  因为 LangGraph 的状态机制需要"字段级 reducer"（每次节点执行后，
  只合并被修改的字段），TypedDict + Annotated 是 LangGraph 的标准做法。
"""

from typing import Annotated, NotRequired, TypedDict

from langchain.agents import AgentState


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    base64: str
    mime_type: str


def merge_sandbox(existing: SandboxState | None, new: SandboxState | None) -> SandboxState | None:
    """Reducer for sandbox state - accepts idempotent writes only.

    Multiple sandbox tools can initialize lazily in the same graph step and
    emit the same sandbox_id via Command(update=...). LangGraph needs an
    explicit reducer for that shared state key. Different sandbox ids in the
    same thread indicate a lifecycle/isolation bug, so fail closed instead of
    choosing one silently.
    """
    if new is None:
        return existing
    if existing is None:
        return new

    existing_id = existing.get("sandbox_id")
    new_id = new.get("sandbox_id")
    if existing_id == new_id:
        return existing
    raise ValueError(f"Conflicting sandbox state updates: {existing_id!r} != {new_id!r}")


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Reducer for artifacts list - merges and deduplicates artifacts.

    yyds: artifacts 的合并策略。每次节点执行完，LangGraph 自动调这个函数
          把新旧列表合并。用 dict.fromkeys 去重并保持顺序（先入的在前）。
    """
    if existing is None:
        return new or []
    if new is None:
        return existing
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """Reducer for viewed_images dict - merges image dictionaries.

    yyds: viewed_images 的合并策略。和 artifacts 类似，但有个特殊设计：
          传入空 dict {} 会清空所有已查看的图片（用于中间件处理后重置）。
          这样中间件注入完图片信息后可以清空，避免重复处理。
    Special case: If new is an empty dict {}, it clears the existing images.
    This allows middlewares to clear the viewed_images state after processing.
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


class PromotedTools(TypedDict):
    catalog_hash: str
    names: list[str]


def merge_promoted(existing: PromotedTools | None, new: PromotedTools | None) -> PromotedTools | None:
    """Reducer for deferred-tool promotions, scoped by catalog hash.

    - new None/empty -> preserve existing (node didn't touch promotions).
    - catalog_hash changed -> replace wholesale, dropping stale names (prevents a
      persisted bare name from exposing a different tool after catalog drift).
    - same catalog_hash -> union names, dedupe, preserve order.
    """
    if not new:
        return existing
    if existing is None or existing.get("catalog_hash") != new["catalog_hash"]:
        return {
            "catalog_hash": new["catalog_hash"],
            "names": list(dict.fromkeys(new["names"])),
        }
    return {
        "catalog_hash": existing["catalog_hash"],
        "names": list(dict.fromkeys(existing["names"] + new["names"])),
    }


class ThreadState(AgentState):

    sandbox: Annotated[NotRequired[SandboxState | None], merge_sandbox]
    """yyds: Agent 的完整状态定义。继承自 LangChain 的 AgentState（已包含 messages 字段）。
    NotRequired 表示这个字段是可选的（可能不存在）。
    Annotated[list[str], merge_artifacts] 表示这个字段用 merge_artifacts 函数做合并。

    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: Annotated[list | None, merge_todos]
    uploaded_files: NotRequired[list[dict] | None]

    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # image_path -> {base64, mime_type}
    promoted: Annotated[PromotedTools | None, merge_promoted]

