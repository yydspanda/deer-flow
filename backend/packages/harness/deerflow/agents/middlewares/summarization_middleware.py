"""yyds: 上下文压缩中间件 — 对话太长时自动压缩旧消息，但保护 skill 内容不被遗忘。

【大白话讲清楚】
  LLM 有记忆窗口限制（比如 128K token）。对话越长，窗口越满。
  这个中间件在每次调用 LLM 前检查一次：窗口快满了？那就把旧消息压缩成一段摘要，
  只保留最近的消息。就像做了一天笔记，晚上花 10 分钟写个总结，明天只看总结。

  但有两个棘手问题：

  问题 A — skill 内容被压掉了：
    Agent 加载的 skill 文件（工作指南）也在旧消息里。
    如果把 skill 消息压掉了，Agent 就"忘了"自己会什么技能。
    → Skill Rescue：压缩时把 skill 消息从"要压缩"区拉到"保留"区。

  问题 B — 压缩前的消息全丢了，记忆系统没机会保存：
    压缩一旦执行，旧消息就被 RemoveAll 删掉了。
    如果不提前"抢救"，用户偏好、关键决策就永远丢了。
    → BeforeSummarization Hook：压缩前触发钩子，让外部代码有机会先处理即将被删的消息。

  这两个问题分别用两套机制解决：
    Skill Rescue → 本文件自己实现（_partition_with_skill_rescue）
    Hook → 本文件只提供"钩子基础设施"（_fire_hooks），
           具体的"抢救到记忆"逻辑在 summarization_hook.py 的 memory_flush_hook 里

【具体例子】
  用户和 Agent 聊了 50 轮，总共 100 条消息、50000 token。

  正常压缩流程：
    100 条消息 → 检测超阈值 → 切割点在第 88 条
    → 前 88 条交给 LLM 生成摘要："用户想做XX，AI已完成YY，还剩ZZ..."
    → 保留最后 12 条 + 摘要
    → 最终 13 条消息、约 2000 token ✅

  Skill 保护场景：
    第 5-6 条消息是 Agent 加载 research skill 的记录（read_file + skill 内容）
    如果压缩时不保护 → 摘要里没有 skill 细节 → Agent "忘了"怎么调研
    → 本中间件把第 5-6 条从"要压缩"区拉到"保留"区
    → skill 内容原样保留在消息列表里 ✅

  Hook 触发场景（"抢救到记忆"的完整三步链路）：
    ① 定义钩子接口（本文件 BeforeSummarizationHook）：
       "任何接受 SummarizationEvent、没有返回值的函数都算合法钩子"
    ② 注册具体钩子（agent.py:153-155）：
       hooks = []
       if memory.enabled:
           hooks.append(memory_flush_hook)  ← 把函数存到列表
       DeerFlowSummarizationMiddleware(before_summarization=hooks)  ← 传进去
    ③ 触发钩子（本文件 _fire_hooks）：
       压缩前到时机了 → for hook in hooks: hook(event) → memory_flush_hook(event)
       → memory_flush_hook 把旧消息入队到记忆系统（summarization_hook.py）
       → 用 add_nowait（0s 延迟）因为消息马上要被删了

  你踩过的 bug：
    压缩时拆分 AIMessage 的 tool_calls，拆分后 ToolMessage 找不到对应的 tool_call
    → LLM 报 400 错误。新建 thread 能规避是因为新对话还没触发压缩。

【基类 vs 本类的分工】
  LangChain 自带的 SummarizationMiddleware（基类）提供核心压缩流程：
    - token 计数、阈值判断、切割点确定
    - 分区（前半压缩、后半保留）
    - 用 LLM 生成摘要
    - 构建新消息列表

  DeerFlow 的 DeerFlowSummarizationMiddleware（本文件）在基类上增加三个能力：
    ① Skill Rescue：压缩时保护 skill 文件内容不被压掉
    ② BeforeSummarization Hooks：压缩前触发钩子（memory_flush_hook 把旧消息存到记忆系统）
    ③ 摘要消息标记 name="summary"：前端看到这个标记就不展示摘要

---

Summarization middleware extensions for DeerFlow.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol, override, runtime_checkable

from langchain.agents import AgentState
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.config import get_config
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.dynamic_context_middleware import is_dynamic_context_reminder
from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummarizationEvent:
    """yyds: 压缩事件的"快递单" — 告诉钩子函数"要压哪些、留哪些"。

    钩子函数（memory_flush_hook）拿到这个数据，就知道该把哪些即将被压掉的消息存到记忆系统。
    """

    messages_to_summarize: tuple[AnyMessage, ...]  # yyds: 即将被压缩掉的旧消息
    preserved_messages: tuple[AnyMessage, ...]  # yyds: 会保留的消息
    thread_id: str | None  # yyds: 当前对话线程 ID
    agent_name: str | None  # yyds: 当前 Agent 名字
    runtime: Runtime  # yyds: LangGraph 运行时


@runtime_checkable
class BeforeSummarizationHook(Protocol):
    """yyds: 压缩前钩子的接口规范 — "什么函数能当钩子用"。

    钩子（Hook）= 主流程在某个时机插入你自己的代码，不用改主流程。
    本质上就是：把函数存到列表 → 到时机遍历调用。

    这个 Protocol 规定："接受 SummarizationEvent、没有返回值的函数"都算合法钩子。
    不管你是普通函数、lambda、还是带 __call__ 的类实例，签名对了就行。

    钩子从注册到触发的完整链路：
      ① agent.py:154 注册：hooks.append(memory_flush_hook)
      ② agent.py:162 创建：DeerFlowSummarizationMiddleware(before_summarization=hooks)
      ③ 本文件 _fire_hooks 触发：for hook in hooks: hook(event)
      ④ summarization_hook.py 执行：把旧消息入队到记忆系统
    """

    def __call__(self, event: SummarizationEvent) -> None: ...


def _resolve_thread_id(runtime: Runtime) -> str | None:
    """yyds: 双路查找线程 ID — runtime.context 里没有就从 LangGraph 配置里找。"""
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        try:
            config_data = get_config()
        except RuntimeError:
            return None
        thread_id = config_data.get("configurable", {}).get("thread_id")
    return thread_id


def _resolve_agent_name(runtime: Runtime) -> str | None:
    """yyds: 双路查找 Agent 名字 — 和 _resolve_thread_id 一样的逻辑。"""
    agent_name = runtime.context.get("agent_name") if runtime.context else None
    if agent_name is None:
        try:
            config_data = get_config()
        except RuntimeError:
            return None
        agent_name = config_data.get("configurable", {}).get("agent_name")
    return agent_name


def _tool_call_path(tool_call: dict[str, Any]) -> str | None:
    """yyds: 从工具调用参数里提取文件路径。

    不同工具用不同的参数名（path / file_path / filepath），三个都试一遍。
    用于判断这次 read_file 读的是不是 skill 文件。
    """
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        return None
    for key in ("path", "file_path", "filepath"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _clone_ai_message(
    message: AIMessage,
    tool_calls: list[dict[str, Any]],
    *,
    content: Any | None = None,
) -> AIMessage:
    """yyds: 克隆 AIMessage 并替换 tool_calls — 拆分 skill tool_calls 时用。

    委托给 tool_call_metadata.py 的 clone_ai_message_with_tool_calls，
    它会同步三处关联数据（tool_calls、additional_kwargs、response_metadata）。
    """
    return clone_ai_message_with_tool_calls(message, tool_calls, content=content)


@dataclass
class _SkillBundle:
    """yyds: 一组 skill 相关的消息 — 用来追踪"读 skill 文件"的完整消息链。

    具体例子：
      AIMessage(tool_calls=[{name:"read_file", args:{path:"/mnt/skills/alpha/SKILL.md"}, id:"t1"}])
      ToolMessage(content="alpha skill 的完整内容...", tool_call_id="t1")
      ↑ 这两条消息组成一个 _SkillBundle

    ai_index: AIMessage 在消息列表的位置
    skill_tool_indices: 对应 ToolMessage 的位置
    skill_tool_call_ids: tool_call ID 集合
    skill_tool_tokens: 这些 ToolMessage 占多少 token
    skill_key: 路径拼接的去重键（同一个 skill 只保护一次）
    """

    ai_index: int
    skill_tool_indices: tuple[int, ...]
    skill_tool_call_ids: frozenset[str]
    skill_tool_tokens: int
    skill_key: str


class DeerFlowSummarizationMiddleware(SummarizationMiddleware):
    """yyds: 上下文压缩中间件 — 窗口快满了就压缩，但 skill 内容不能丢。

    决策树（每次 LLM 调用前执行一次）：

      总 token 超阈值了？
      ├─ 没有 → 返回 None（不压缩，跳过）
      └─ 超了 → 切割点在哪？
          ├─ 切割点 ≤ 0（说明消息太少，没法切）→ 返回 None
          └─ 合法 → 执行压缩流程 ↓

      压缩流程：
        ① 基类分区：按切割点切一刀 → [旧消息] [新消息]
        ② Skill Rescue：从旧消息里找 skill bundle，拉回保留区
           ├─ AIMessage 只调了 skill 工具 → 整条救回来
           ├─ AIMessage 同时调了 skill 和非 skill → 拆成两条（一条救，一条继续压缩）
           └─ 超预算的 skill（太大/太多）→ 不救了，放弃保护
        ③ 保护动态上下文提醒（日期/记忆）不被压掉
        ④ 触发钩子：memory_flush_hook 把即将压掉的消息存到记忆系统
        ⑤ 用 LLM 把剩余的旧消息压缩成摘要
        ⑥ 返回：[RemoveAll（清空旧消息）, 摘要, skill 消息, 最近消息]

    Demo 时序（12 条消息，阈值 10 条，保留最后 4 条）：

      压缩前：
        [0] HumanMessage: "帮我调研 LangGraph"
        [1] AIMessage: tool_calls=[read_file("/mnt/skills/research/SKILL.md")]
        [2] ToolMessage: "research skill 内容..."（3000 token）
        [3-7] 用户对话和搜索结果...
        [8-11] 最近 4 条对话

      压缩过程：
        ① 基类分区：to_summarize=[0-7], preserved=[8-11]
        ② Skill Rescue：在 [0-7] 里找到消息 [1,2] 是 skill bundle → 拉到 preserved
        ③ 最终：to_summarize=[0,3,4,5,6,7], preserved=[1,2,8,9,10,11]
        ④ LLM 生成摘要："用户想调研 LangGraph，已搜索初步资料..."
        ⑤ 返回：[RemoveAll, 摘要, AIMessage(skill), ToolMessage(skill), 消息8-11]
    """

    def __init__(
        self,
        *args,
        skills_container_path: str | None = None,
        skill_file_read_tool_names: Collection[str] | None = None,
        before_summarization: list[BeforeSummarizationHook] | None = None,
        preserve_recent_skill_count: int = 5,
        preserve_recent_skill_tokens: int = 25_000,
        preserve_recent_skill_tokens_per_skill: int = 5_000,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._skills_container_path = skills_container_path or "/mnt/skills"
        self._skill_file_read_tool_names = frozenset(skill_file_read_tool_names or {"read_file", "read", "view", "cat"})
        self._before_summarization_hooks = before_summarization or []  # yyds: 压缩前钩子列表。在 agent.py:153-155 注册，当前只有 memory_flush_hook
        self._preserve_recent_skill_count = max(0, preserve_recent_skill_count)  # yyds: 最多保护 N 个 skill bundle
        self._preserve_recent_skill_tokens = max(0, preserve_recent_skill_tokens)  # yyds: 被保护的 skill 总 token 上限
        self._preserve_recent_skill_tokens_per_skill = max(0, preserve_recent_skill_tokens_per_skill)  # yyds: 单个 skill 的 token 上限

    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._maybe_summarize(state, runtime)

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return await self._amaybe_summarize(state, runtime)

    def _maybe_summarize(self, state: AgentState, runtime: Runtime) -> dict | None:
        """yyds: 压缩入口 — 每次 LLM 调用前执行，大部分时候不触发。

        具体例子（第 50 轮对话，共 100 条消息、50000 token）：
          ① 检查每条消息有没有 id（LangGraph 用 id 做消息替换/追加判断）
          ② 算 token 数 → 50000 超过阈值 → 继续往下
          ③ 切割点 = 88（保留最后 12 条）
          ④ 分区 + skill 保护：消息 [0-87] 要压缩，消息 [88-99] 保留
             → 如果消息 [3-4] 是 skill bundle → 拉回保留区
          ⑤ 保护动态上下文提醒（日期/记忆）不被压掉
          ⑥ 触发钩子：memory_flush_hook 把 [0-87] 里的关键信息存到记忆
          ⑦ LLM 把剩余旧消息生成摘要
          ⑧ 返回：[RemoveAll, 摘要, skill 消息, 最近消息]
        """
        messages = state["messages"]
        self._ensure_message_ids(messages)  # yyds: 补 id（没有的话 LangGraph 不知道怎么替换）

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None  # yyds: 没超阈值，跳过

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None  # yyds: 切割点不合法（消息太少没法切）

        messages_to_summarize, preserved_messages = self._partition_with_skill_rescue(messages, cutoff_index)
        messages_to_summarize, preserved_messages = self._preserve_dynamic_context_reminders(messages_to_summarize, preserved_messages)
        self._fire_hooks(messages_to_summarize, preserved_messages, runtime)  # yyds: 触发钩子 → memory_flush_hook 把旧消息入队到记忆（add_nowait 0s 延迟）
        summary = self._create_summary(messages_to_summarize)
        new_messages = self._build_new_messages(summary)

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),  # yyds: 先清空所有旧消息
                *new_messages,  # yyds: 摘要
                *preserved_messages,  # yyds: skill 消息 + 最近消息
            ]
        }

    async def _amaybe_summarize(self, state: AgentState, runtime: Runtime) -> dict | None:
        """yyds: 异步版压缩入口 — 和同步版一样，只是 LLM 生成摘要用 await。"""
        messages = state["messages"]
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        messages_to_summarize, preserved_messages = self._partition_with_skill_rescue(messages, cutoff_index)
        messages_to_summarize, preserved_messages = self._preserve_dynamic_context_reminders(messages_to_summarize, preserved_messages)
        self._fire_hooks(messages_to_summarize, preserved_messages, runtime)  # yyds: 同步版和异步版都触发同一个钩子
        summary = await self._acreate_summary(messages_to_summarize)
        new_messages = self._build_new_messages(summary)

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }

    @override
    def _build_new_messages(self, summary: str) -> list[HumanMessage]:
        """yyds: 覆写基类 — 给摘要消息加 name="summary" 标记。

        没有这个标记的话，前端会把摘要当成用户消息展示出来（乱糟糟的）。
        加了 name="summary" 后，前端看到就知道"这是压缩摘要，不展示"。
        但 LLM 在后续对话中能看到这个摘要（相当于"前情提要"）。
        """
        return [HumanMessage(content=f"Here is a summary of the conversation to date:\n\n{summary}", name="summary")]

    def _preserve_dynamic_context_reminders(
        self,
        messages_to_summarize: list[AnyMessage],
        preserved_messages: list[AnyMessage],
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """yyds: 保护动态上下文提醒（日期/记忆）不被压掉。

        DynamicContextMiddleware 会在对话开头注入一条隐藏的日期提醒。
        如果这条消息被压掉了，下一轮它会把提醒插到摘要消息前面 → 位置错乱。
        所以把日期提醒从"要压缩"区移到"保留"区。

        具体例子：
          压缩前：[日期提醒, 用户消息1, AI回复1, ..., 用户消息N]
          没保护 → to_summarize=[日期提醒, 消息1, ...], preserved=[..., 消息N]
                   → 日期提醒被压掉，下一轮 DynamicContext 又插一条 → 重复了
          有保护 → to_summarize=[消息1, ...], preserved=[日期提醒, ..., 消息N] ✅
        """
        reminders = [msg for msg in messages_to_summarize if is_dynamic_context_reminder(msg)]
        if not reminders:
            return messages_to_summarize, preserved_messages

        remaining = [msg for msg in messages_to_summarize if not is_dynamic_context_reminder(msg)]
        return remaining, reminders + preserved_messages

    def _partition_with_skill_rescue(
        self,
        messages: list[AnyMessage],
        cutoff_index: int,
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """yyds: Skill Rescue 核心 — 从"要压缩"区把 skill 消息救回来。

        为什么需要这个：skill 内容是 Agent 的工作指南，压掉了就"忘了"怎么干活。

        棘手的地方：一条 AIMessage 可能同时调了 skill 工具和非 skill 工具。
        这时需要把 AIMessage 拆成两条：一条只保留 skill tool_calls（被救），
        一条保留非 skill tool_calls（继续被压缩）。

        你踩过的 bug：拆分后 ToolMessage 找不到对应的 AIMessage(tool_calls)
        → 孤儿 ToolMessage → LLM 报 400。新建 thread 能规避是因为还没触发压缩。

        具体例子（消息 [3] 同时调了 read_file(skill) + search(非 skill)）：
          压缩前 to_summarize = [
            [0] HumanMessage,
            [1] AIMessage: tool_calls=[read_file(skill), search(普通)],  ← 要拆
            [2] ToolMessage(skill 内容),
            [3] ToolMessage(搜索结果),
            ...
          ]
          拆分后：
            remaining = [HumanMessage, AIMessage(只有 search), ToolMessage(搜索结果)]
            rescued  = [AIMessage(只有 read_file skill), ToolMessage(skill 内容)]
        """
        to_summarize, preserved = self._partition_messages(messages, cutoff_index)  # yyds: 基类分区：[0..cutoff) 压缩，[cutoff..end] 保留

        if self._preserve_recent_skill_count == 0 or self._preserve_recent_skill_tokens == 0 or not to_summarize:  # yyds: skill 保护关闭 or 没有要压缩的消息
            return to_summarize, preserved

        try:
            bundles = self._find_skill_bundles(to_summarize, self._skills_container_path)
        except Exception:  # yyds: 出错就放弃 skill 保护，走基类默认分区（不因为 skill 保护失败而中断压缩）
            logger.exception("Skill-preserving summarization rescue failed; falling back to default partition")
            return to_summarize, preserved

        if not bundles:
            return to_summarize, preserved

        rescue_bundles = self._select_bundles_to_rescue(bundles)  # yyds: 按预算筛选（从最新开始挑，最多 5 个、总共 25000 token）
        if not rescue_bundles:
            return to_summarize, preserved

        bundles_by_ai_index = {bundle.ai_index: bundle for bundle in rescue_bundles}
        rescue_tool_indices = {idx for bundle in rescue_bundles for idx in bundle.skill_tool_indices}
        rescued: list[AnyMessage] = []
        remaining: list[AnyMessage] = []
        for i, msg in enumerate(to_summarize):
            bundle = bundles_by_ai_index.get(i)
            if bundle is not None and isinstance(msg, AIMessage):  # yyds: 这条 AIMessage 包含 skill 调用 → 可能要拆分
                rescued_tool_calls = [tc for tc in msg.tool_calls if tc.get("id") in bundle.skill_tool_call_ids]
                remaining_tool_calls = [tc for tc in msg.tool_calls if tc.get("id") not in bundle.skill_tool_call_ids]

                if rescued_tool_calls:
                    rescued.append(_clone_ai_message(msg, rescued_tool_calls, content=""))  # yyds: 被救的版本：只保留 skill tool_calls，content 清空省 token
                if remaining_tool_calls or msg.content:
                    remaining.append(_clone_ai_message(msg, remaining_tool_calls))  # yyds: 继续被压缩的版本：保留非 skill tool_calls 和原始 content
                continue

            if i in rescue_tool_indices:  # yyds: 这是 skill 对应的 ToolMessage → 移到保留区
                rescued.append(msg)
                continue

            remaining.append(msg)  # yyds: 和 skill 无关的消息 → 继续被压缩

        return remaining, rescued + preserved

    def _find_skill_bundles(
        self,
        messages: list[AnyMessage],
        skills_root: str,
    ) -> list[_SkillBundle]:
        """yyds: 在消息列表里找所有"读 skill 文件"的消息组。

        怎么判断"读 skill 文件"：
          ① 工具名是 read_file（或配置的其他读取工具名）
          ② 路径以 /mnt/skills/ 开头

        具体例子（消息列表里有一组 skill 读取）：
          [3] AIMessage: tool_calls=[{name:"read_file", args:{path:"/mnt/skills/research/SKILL.md"}, id:"tc1"}]
          [4] ToolMessage: "research skill 的完整内容...", tool_call_id="tc1"
          ↑ 这两条组成一个 _SkillBundle(ai_index=3, skill_tool_indices=(4,), ...)

        扫描逻辑：从头到尾遍历消息列表，找到 AIMessage 里含有 skill tool_call 的，
        然后往后找紧跟着的 ToolMessage 匹配 tool_call_id。
        """
        bundles: list[_SkillBundle] = []
        n = len(messages)
        i = 0
        while i < n:
            msg = messages[i]
            if not (isinstance(msg, AIMessage) and msg.tool_calls):  # yyds: 跳过没有 tool_calls 的消息
                i += 1
                continue

            tool_calls = list(msg.tool_calls)
            skill_paths_by_id: dict[str, str] = {}  # yyds: {tool_call_id: skill路径}
            for tc in tool_calls:
                if self._is_skill_tool_call(tc, skills_root):
                    tc_id = tc.get("id")
                    path = _tool_call_path(tc)
                    if tc_id and path:
                        skill_paths_by_id[tc_id] = path

            if not skill_paths_by_id:  # yyds: 这条 AIMessage 里没有 skill 调用
                i += 1
                continue

            skill_tool_tokens = 0
            skill_key_parts: list[str] = []
            skill_tool_indices: list[int] = []
            matched_skill_call_ids: set[str] = set()

            j = i + 1
            while j < n and isinstance(messages[j], ToolMessage):  # yyds: ToolMessage 总是紧跟 AIMessage
                j += 1

            for k in range(i + 1, j):
                tool_msg = messages[k]
                if isinstance(tool_msg, ToolMessage) and tool_msg.tool_call_id in skill_paths_by_id:
                    skill_tool_tokens += self.token_counter([tool_msg])
                    skill_key_parts.append(skill_paths_by_id[tool_msg.tool_call_id])
                    skill_tool_indices.append(k)
                    matched_skill_call_ids.add(tool_msg.tool_call_id)

            if not skill_tool_indices:  # yyds: 有 skill tool_call 但没找到对应的 ToolMessage（不应该发生）
                i = j
                continue

            bundles.append(
                _SkillBundle(
                    ai_index=i,
                    skill_tool_indices=tuple(skill_tool_indices),
                    skill_tool_call_ids=frozenset(matched_skill_call_ids),
                    skill_tool_tokens=skill_tool_tokens,
                    skill_key="|".join(sorted(skill_key_parts)),  # yyds: 路径排序后用 | 拼接，用于同一个 skill 去重
                )
            )
            i = j  # yyds: 跳过已处理的 ToolMessage，继续扫描

        return bundles

    def _select_bundles_to_rescue(self, bundles: list[_SkillBundle]) -> list[_SkillBundle]:
        """yyds: 按"预算"选要救哪些 skill bundle — 从最新的开始挑，超预算就跳过。

        三重预算限制（像超市购物有预算一样）：
          - 购物车最多装 5 个（preserve_recent_skill_count）
          - 总价不超过 25000 token（preserve_recent_skill_tokens）
          - 单件不超过 5000 token（preserve_recent_skill_tokens_per_skill）
          - 同一种 skill 只买一份（去重）

        具体例子（3 个 skill bundle，按时间从早到晚排列）：
          bundle_A: research skill, 4000 token  ← 较早
          bundle_B: coding skill, 6000 token     ← 单件超 5000，跳过
          bundle_C: research skill, 3000 token   ← 和 A 同名，去重跳过
          bundle_D: writing skill, 2000 token    ← 最新

          从最新开始挑：D(2000) → A(4000) → 总共 6000 token，2 个，不超预算 ✅
        """
        selected: list[_SkillBundle] = []
        if not bundles:
            return selected

        seen_skill_keys: set[str] = set()  # yyds: 已选的 skill 标识（去重）
        total_tokens = 0
        kept = 0

        for bundle in reversed(bundles):  # yyds: 从最新的开始挑（最新的 skill 更可能是当前在用的）
            if kept >= self._preserve_recent_skill_count:
                break
            if bundle.skill_key in seen_skill_keys:  # yyds: 同一个 skill 已选过
                continue
            if bundle.skill_tool_tokens > self._preserve_recent_skill_tokens_per_skill:  # yyds: 单个太大，救了也浪费
                continue
            if total_tokens + bundle.skill_tool_tokens > self._preserve_recent_skill_tokens:  # yyds: 加上就超总预算
                continue

            selected.append(bundle)
            total_tokens += bundle.skill_tool_tokens
            kept += 1
            seen_skill_keys.add(bundle.skill_key)

        selected.reverse()  # yyds: 反转回原始时间顺序，保证消息列表顺序正确
        return selected

    def _is_skill_tool_call(self, tool_call: dict[str, Any], skills_root: str) -> bool:
        """yyds: 判断一次工具调用是不是"读 skill 文件" — 工具名匹配 + 路径匹配。"""
        name = tool_call.get("name") or ""
        if name not in self._skill_file_read_tool_names:  # yyds: 不是读取工具名
            return False
        path = _tool_call_path(tool_call)
        if not path:
            return False
        normalized_root = skills_root.rstrip("/")
        return path == normalized_root or path.startswith(normalized_root + "/")

    def _fire_hooks(
        self,
        messages_to_summarize: list[AnyMessage],
        preserved_messages: list[AnyMessage],
        runtime: Runtime,
    ) -> None:
        """yyds: 触发压缩前钩子 — 没有魔法，就是存函数、到时机调一下。

        钩子机制的本质（三步）：
          ① 注册（agent.py:153-155）：
             hooks = []
             if memory.enabled: hooks.append(memory_flush_hook)  ← 把函数存到列表
          ② 传入本文件（agent.py:162）：
             DeerFlowSummarizationMiddleware(before_summarization=hooks)
             → 存到 self._before_summarization_hooks
          ③ 到时机触发（本函数）：
             for hook in hooks: hook(event)  ← 遍历调用
             → memory_flush_hook(event) 执行
             → summarization_hook.py 把旧消息入队到记忆系统（add_nowait，0s 延迟）

        为什么叫"钩子"不叫"回调"：语义偏好。hook 强调"在主流程的某个点插入"，
        callback 强调"完成后通知"。本质没区别，代码一模一样。

        钩子失败不影响压缩（try/except 兜底，只记日志，继续调下一个钩子）。
        没有注册任何钩子 → 直接 return，主流程照跑。
        """
        if not self._before_summarization_hooks:
            return

        event = SummarizationEvent(
            messages_to_summarize=tuple(messages_to_summarize),  # yyds: 即将被压掉的旧消息
            preserved_messages=tuple(preserved_messages),  # yyds: 会保留的消息
            thread_id=_resolve_thread_id(runtime),
            agent_name=_resolve_agent_name(runtime),
            runtime=runtime,
        )

        for hook in self._before_summarization_hooks:
            try:
                hook(event)  # yyds: 调 memory_flush_hook(event)，它把旧消息入队到记忆系统
            except Exception:  # yyds: 钩子失败不影响压缩（只记日志，继续调下一个钩子）
                hook_name = getattr(hook, "__name__", None) or type(hook).__name__
                logger.exception("before_summarization hook %s failed", hook_name)
