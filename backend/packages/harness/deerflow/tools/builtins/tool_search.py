"""yyds: 延迟工具发现 — "先看目录，按需下单"的 MCP 工具加载策略。

【大白话讲清楚】
  问题：MCP 服务器可能注册几十甚至上百个工具。每个工具的 OpenAI function schema
  大约 50-200 tokens。100 个工具 = 5000-20000 tokens，全塞给 LLM 的话：
    - 浪费 context window（LLM 一轮对话可能只用 3-5 个工具）
    - 浪费钱（每轮都带着这些 schema 计费）
    - 降低 LLM 决策质量（选择太多反而选不好）

  解决方案：两阶段加载 —
    第一阶段（注册但不绑定）：MCP 工具注册到 DeferredToolRegistry，LLM 只看到名字列表：
      <available-deferred-tools>
      slack_send_message
      slack_read_channel
      github_create_issue
      jira_create_ticket
      ...（100 个工具名，每个只占几个 token）
      </available-deferred-tools>

    第二阶段（按需获取 schema）：LLM 决定要用某个工具时，先调 tool_search 获取完整 schema，
    然后才能调用。

  三方协作：
    ① DeferredToolRegistry（本文件）— 存储"延迟工具"，提供搜索/提升功能
    ② DeferredToolFilterMiddleware — 拦截器，阻止延迟工具的 schema 发给 LLM
    ③ prompt.py 的 get_deferred_tools_prompt_section — 把名字列表注入 system prompt

【具体例子】
  配置：config.yaml 的 tool_search.enabled=true，MCP 服务器注册了 80 个工具。

  第 1 轮（LLM 看到名字列表）：
    system prompt 里有：
      <available-deferred-tools>
      slack_send_message
      slack_read_channel
      github_list_prs
      ...
      </available-deferred-tools>
    + tool_search 工具（LLM 可以调用来搜索）

  第 2 轮（用户："给 #general 发一条 Slack 消息"）：
    LLM 想：我需要 slack_send_message 的完整参数 schema
    → 调用 tool_search("select:slack_send_message")
    → registry.search() 找到匹配 → promote（提升为活跃工具）
    → 返回 JSON schema：{"name": "slack_send_message", "parameters": {"channel": ..., "text": ...}}
    → DeferredToolFilterMiddleware 不再过滤这个工具

  第 3 轮（LLM 现在能调用了）：
    → 调用 slack_send_message(channel="#general", text="新版本发布了！")
    → 成功 ✅

  异常流程 A（LLM 偷偷调了没 promote 的工具）：
    LLM 直接调用 jira_create_ticket（没先 tool_search）
    → DeferredToolFilterMiddleware 拦截
    → 返回错误 ToolMessage："先调 tool_search 来激活这个工具"
    → LLM 学到要先搜索再调用

  异常流程 B（搜索没找到）：
    tool_search("我能飞吗")
    → registry.search() 匹配不到任何工具
    → 返回 "No tools found matching: 我能飞吗"

【加载条件】
  ① config.yaml 的 tool_search.enabled = true
  ② MCP 服务器注册了工具（extensions.json 有启用的 MCP server）
  两个条件都满足时，get_available_tools() 才会：
    - 创建 DeferredToolRegistry 并注册所有 MCP 工具
    - 把 tool_search 工具加入 builtin_tools
    - 注入 DeferredToolFilterMiddleware 到中间件链

【ContextVar 隔离】
  用 contextvars.ContextVar 存 registry，不是全局变量。
  → 每个 async 请求有独立的 registry（并发请求互不干扰）
  → sub-agent 继承父 agent 的 registry（不会丢掉已 promote 的工具）

---
Tool search — deferred tool discovery at runtime.

Contains:
- DeferredToolCatalog: immutable, searchable catalog of deferred tools.
- build_tool_search_tool: builds the `tool_search` tool as a closure over a
  catalog; it records promotions into graph state via ``Command``.
- build_deferred_tool_setup: assembles the catalog + tool from a
  policy-filtered tool list (call AFTER tool-policy filtering).

The agent sees deferred tool names in <available-deferred-tools> but cannot
call them until it fetches their full schema via the tool_search tool. The
deferred set rides on a build-time closure and promotion lives in per-thread
graph state — there is no ContextVar. Source-agnostic: a tool is "deferred"
when it carries the ``deerflow_mcp`` metadata tag.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated

from langchain.tools import BaseTool
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langchain_core.utils.function_calling import convert_to_openai_function
from langgraph.types import Command

from deerflow.tools.mcp_metadata import is_mcp_tool

logger = logging.getLogger(__name__)

MAX_RESULTS = 5  # yyds: 每次搜索最多返回 5 个工具（防止一次拉太多 schema 回来又撑爆 context）


def _compile_catalog_regex(pattern: str) -> re.Pattern[str]:
    """Compile ``pattern`` case-insensitively, falling back to a literal match.

    Search queries come from the model, so an invalid regex (e.g. an unbalanced
    paren) must degrade to a literal substring match rather than raise.
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)




# ── Catalog ──


# NOTE: frozen=True without slots=True keeps __dict__, which is what lets the
# @cached_property fields below cache (they write to instance.__dict__, bypassing
# the frozen __setattr__). Do NOT add slots=True or hash/names break at runtime.
@dataclass(frozen=True)
class DeferredToolCatalog:
    """Immutable catalog of deferred tools. Pure search, no mutation."""
# yyds: 延迟工具条目，存储工具名、描述和完整工具对象（仅在搜索匹配时返回）
# yyds: 延迟工具注册中心，支持正则/关键词/精确名三种搜索模式，匹配后自动提升为活跃工具
    """yyds: 延迟工具条目 — 轻量级包装，只存名字/描述/工具对象。
    为什么不直接存 BaseTool？
    因为 search() 需要按 name+description 匹配，提前提取出来避免反复调属性。
    工具对象只在匹配成功后才返回给 LLM。
    """yyds: 延迟工具注册中心 — "工具仓库"，搜索到就提升为可调用状态。
    三种搜索方式（对齐 Claude Code 的设计）：
      查询字符串                     匹配逻辑                         例子
      "select:name1,name2"           精确选择，按名字直接拿            select:slack_send,github_pr
      "+keyword rest"                name 必须包含 keyword，按 rest 排序  +slack send message
      "keyword query"                正则匹配 name + description       slack.*message
    生命周期：
      注册 → 搜索匹配 → promote（提升）→ 不再被 DeferredToolFilterMiddleware 过滤
      register(tools)  →  search(query)  →  promote(names)  →  工具变成"活跃"状态




    tools: tuple[BaseTool, ...]


    @cached_property
    def names(self) -> frozenset[str]:
        return frozenset(t.name for t in self.tools)

    @cached_property
    def hash(self) -> str:
        canon = [{"name": t.name, "schema": convert_to_openai_function(t)} for t in sorted(self.tools, key=lambda t: t.name)]
        blob = json.dumps(canon, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    # yyds: 注册一个延迟工具，提取名称和描述用于后续搜索
    # yyds: 将匹配的工具从延迟列表提升为活跃状态，后续不再被DeferredToolFilterMiddleware过滤
        """yyds: 注册一个延迟工具 — 只存元数据，schema 不会发给 LLM。"""
        """yyds: 提升工具 — 从延迟列表移除，后续不再被过滤。
        什么时候调？
        tool_search 返回工具 schema 后立即调用。
        下一次 bind_tools 时，DeferredToolFilterMiddleware 的 deferred_names 里
        已经没有这些名字了 → schema 正常发给 LLM → LLM 可以调用。
        为什么是"移除"而不是"标记"？
        因为 _entries 只存延迟工具，移除 = 不再延迟 = 活跃。
        简单粗暴，不需要额外的 is_active 字段。


    def search(self, query: str) -> list[BaseTool]:

        query = query.strip()
        if not query:
            return []
        """yyds: 核心搜索 — 三种查询模式，匹配后返回工具对象。
        模式 ① "select:slack_send,github_pr"
          → 精确选择，按逗号分割，直接拿对应的工具
          → 场景：LLM 看了名字列表，已经知道要哪个，直接选
        模式 ② "+slack send message"
          → name 必须包含 "slack"，然后按 "send message" 排序
          → 场景：LLM 知道要 Slack 相关的，但不确定具体哪个
        模式 ③ "slack.*message" 或 "发送消息"
          → 正则匹配 name + description
          → 名称匹配得 2 分，描述匹配得 1 分 → 按分数排序
          → 场景：LLM 用自然语言描述需求
          → 正则语法错误时自动 escape，不会崩

        if query.startswith("select:"):
            wanted = {n.strip() for n in query[7:].split(",")}
            return [t for t in self.tools if t.name in wanted][:MAX_RESULTS]

        if query.startswith("+"):
            parts = query[1:].split(None, 1)
            if not parts:
                return []  # bare "+" with no required token — nothing to require
            required = parts[0].lower()
            candidates = [t for t in self.tools if required in t.name.lower()]
            if len(parts) > 1:
                candidates.sort(key=lambda t: _catalog_regex_score(parts[1], t), reverse=True)
            return candidates[:MAX_RESULTS]


        regex = _compile_catalog_regex(query)
        scored: list[tuple[int, BaseTool]] = []
        for t in self.tools:
            searchable = f"{t.name} {t.description or ''}"
        # yyds: 通用正则搜索 — name 匹配权重更高（2 分 vs 1 分）

            if regex.search(searchable):
                scored.append((2 if regex.search(t.name) else 1, t))
        scored.sort(key=lambda x: x[0], reverse=True)

        return [t for _, t in scored][:MAX_RESULTS]



def _catalog_regex_score(pattern: str, t: BaseTool) -> int:
    regex = _compile_catalog_regex(pattern)
    return len(regex.findall(f"{t.name} {t.description or ''}"))
# yyds: 计算正则匹配得分，用于搜索结果排序
        """yyds: 当前还在延迟状态的工具名集合。
        DeferredToolFilterMiddleware 每次都调这个属性来决定过滤谁。
        promote 之后名字就从这里消失了 → 不再被过滤。
        """yyds: 某个工具是否还在延迟状态 — 用于拦截直接调用。"""
    """yyds: 计算正则匹配次数，用于搜索结果排序 — 匹配越多排名越前。"""
# yyds: 为什么用 ContextVar 而不是全局变量？
#   LangGraph 每个图运行在独立的 async context 里，多个用户同时发消息时：
#     用户 A 的 registry 里可能有 50 个 MCP 工具
#     用户 B 的 registry 里可能有 30 个不同的 MCP 工具
#   用全局变量 → 互相覆盖 → bug
#   用 ContextVar → 每个请求独立 → 安全
#   sub-agent 场景：ContextVar 值会被复制到 worker 线程
#   → sub-agent 共享父 agent 的 registry → 不丢失已 promote 的工具


# ── Setup / tool ──




@dataclass(frozen=True)
class DeferredToolSetup:
    """Result of assembling deferred-tool support for one agent build.
# yyds: 获取当前异步上下文的延迟工具注册中心


    The three fields move as a unit, so callers branch on ``tool_search_tool``:


    - **Empty** ``(None, frozenset(), None)``: deferral is disabled, or no MCP
      tool survived policy filtering. Nothing is deferred — bind tools as-is.
    - **Populated**: ``tool_search_tool`` is appended to the agent's tools,
      ``deferred_names`` are withheld from the model until promoted, and
      ``catalog_hash`` scopes those promotions in graph state.

    Invariant: ``tool_search_tool is None`` ⟺ ``deferred_names`` is empty ⟺
    ``catalog_hash is None``.
# yyds: 设置当前异步上下文的延迟工具注册中心
# yyds: 重置当前异步上下文的延迟工具注册中心，防止跨请求状态污染
# yyds: 延迟工具搜索工具，Agent调用后获取匹配工具的完整OpenAI function schema并自动提升为可调用状态
    """yyds: 获取当前请求的 registry — 其他中间件/工具都调这个。"""
    """yyds: 设置当前请求的 registry — get_available_tools() 初始化时调用。"""
    """yyds: 重置当前请求的 registry — 请求结束时清理，防止下次请求残留。"""
    """yyds: 延迟工具搜索工具 — LLM 调这个来"解锁"它想用的 MCP 工具。
    执行步骤：
      ① 从 ContextVar 拿到当前请求的 registry
      ② registry.search(query) 搜索匹配的工具
      ③ 把匹配的工具转成 OpenAI function schema（JSON 格式）
      ④ promote 匹配的工具（从延迟列表移除）
      ⑤ 返回 schema JSON → LLM 下一轮就能调用这些工具了
        query: 搜索查询。三种格式：
            "select:name1,name2" — 精确选择
            "+keyword rest" — 关键词筛选 + 排序
            "keyword query" — 正则搜索
        匹配工具的 OpenAI function schema JSON 数组。

    """

    tool_search_tool: BaseTool | None
    deferred_names: frozenset[str]
    catalog_hash: str | None



def build_tool_search_tool(catalog: DeferredToolCatalog) -> BaseTool:
    catalog_hash = catalog.hash
    # yyds: 转成 OpenAI function 格式 — 所有 LLM 都认这个标准 schema
    # yyds: promote = 从延迟列表移除 → 下次 bind_tools 时不再被过滤


    @tool
    def tool_search(query: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Fetches full schema definitions for deferred tools so they can be called.

        Deferred tools appear by name in <available-deferred-tools> in the system
        prompt. Until fetched, only the name is known. This tool matches a query
        against the deferred tools and returns the matched tools complete schemas;
        once returned, a tool becomes callable.

        Query forms:
          - "select:Read,Edit" -- fetch these exact tools by name
          - "notebook jupyter" -- keyword search, up to max_results best matches
          - "+slack send" -- require "slack" in the name, rank by remaining terms
        """
        matched = catalog.search(query)[:MAX_RESULTS]
        if not matched:
            content, names = f"No tools found matching: {query}", []
        else:
            content = json.dumps([convert_to_openai_function(t) for t in matched], indent=2, ensure_ascii=False)
            names = [t.name for t in matched]
        return Command(
            update={
                "promoted": {"catalog_hash": catalog_hash, "names": names},
                "messages": [ToolMessage(content=content, tool_call_id=tool_call_id, name="tool_search")],
            }
        )

    return tool_search


def build_deferred_tool_setup(filtered_tools: list[BaseTool], *, enabled: bool) -> DeferredToolSetup:
    """Build the deferred-tool setup from a POLICY-FILTERED tool list.

    Must be called after skill/agent tool-policy filtering so the catalog never
    exposes a tool the current agent is not allowed to use.

    Returns an empty setup (see :class:`DeferredToolSetup`) in two distinct
    cases: deferral is disabled, or it is enabled but no MCP tool survived
    filtering.
    """
    if not enabled:
        # Deferral disabled: defer nothing; the model binds every tool as before.
        return DeferredToolSetup(None, frozenset(), None)
    deferred = [t for t in filtered_tools if is_mcp_tool(t)]
    if not deferred:
        # Enabled, but no MCP tool to defer: same empty result, different reason.
        return DeferredToolSetup(None, frozenset(), None)
    catalog = DeferredToolCatalog(tuple(deferred))
    return DeferredToolSetup(build_tool_search_tool(catalog), catalog.names, catalog.hash)


def assemble_deferred_tools(filtered_tools: list[BaseTool], *, enabled: bool) -> tuple[list[BaseTool], DeferredToolSetup]:
    """Build the final tool list + deferred setup from a POLICY-FILTERED list.

    Call AFTER tool-policy filtering so the deferred catalog never exposes a tool
    the agent is not allowed to use. Fail-closed: if tool_search is enabled and
    MCP tools survived filtering but no deferred set was recovered, raise rather
    than silently binding their full schemas to the model.

    Shared by every agent-build path (lead, embedded client, subagent) so they
    all get the same fail-closed guarantee from one place.
    """
    deferred_setup = build_deferred_tool_setup(filtered_tools, enabled=enabled)
    if enabled and not deferred_setup.deferred_names and any(is_mcp_tool(t) for t in filtered_tools):
        raise RuntimeError("tool_search enabled and MCP tools survived policy filtering, but no deferred set was recovered - refusing to bind MCP schemas (fail-closed).")
    final_tools = list(filtered_tools)
    if deferred_setup.tool_search_tool:
        final_tools.append(deferred_setup.tool_search_tool)
    return final_tools, deferred_setup


# Prompt rendering


def get_deferred_tools_prompt_section(*, deferred_names: frozenset[str] = frozenset()) -> str:
    """Generate <available-deferred-tools> from an explicit deferred-name set.

    Lists only names so the agent knows what exists and can use tool_search to
    load them. Returns empty string when there are no deferred tools. The set is
    computed at agent build time (after tool-policy filtering) and passed in.

    Lives here, next to the assembly that produces ``deferred_names``, so every
    agent-build path (lead, embedded client, subagent) renders the section the
    same way without coupling back to ``lead_agent.prompt``.
    """
    if not deferred_names:
        return ""
    names = "\n".join(sorted(deferred_names))
    return f"<available-deferred-tools>\n{names}\n</available-deferred-tools>"
