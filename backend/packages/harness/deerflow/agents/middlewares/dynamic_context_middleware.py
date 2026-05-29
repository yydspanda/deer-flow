"""yyds: 动态上下文注入中间件 — 悄悄把"今天是几号"和"用户记忆"塞进对话里。

【做什么】Agent 执行前，在用户消息前面插一条"便签"（<system-reminder>），
   告诉 LLM 当前日期和用户记忆。用户看不到这条便签（hide_from_ui=True）。
【为什么存在】LLM 需要知道"今天是几号"（否则调 API 不知道查哪天）
   和"关于这个用户我记得什么"（否则没有个性化）。
   但 system prompt 是固定的（所有用户共享，为了让 LLM 供应商缓存它，省钱 90%），
   不能往里写"今天是5月13号"——换个用户或过一天就变了，缓存就废了。
   所以把动态内容塞到 HumanMessage 里，system prompt 保持不变。
【在链中的位置】before_agent 阶段执行（在 agent.py 的 _build_middlewares 中第 ② 步追加）。
【关键设计】
   - 首轮注入：完整便签（记忆 + 日期），冻结在第一条消息中，后续轮次不再改
   - 跨午夜检测：对话跨越午夜时，插一条轻量日期更新（不含记忆）
   - ID 交换技巧：利用 LangGraph 的"相同 id 替换，不同 id 追加"机制，
     在用户消息前面精确插入便签（详见类 docstring 的图解）
   - hide_from_ui=True：前端不展示便签，只展示用户消息
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"<current_date>([^<]+)</current_date>")  # yyds: 从消息内容提取已注入日期的正则
_DYNAMIC_CONTEXT_REMINDER_KEY = "dynamic_context_reminder"  # yyds: additional_kwargs 中的标记键，用于识别本中间件注入的消息
_SUMMARY_MESSAGE_NAME = "summary"  # yyds: summarization 中间件生成的摘要消息 name，不对其注入上下文


def _extract_date(content: str) -> str | None:
    """yyds: 从消息内容中提取 <current_date> 标签的值。"""
    m = _DATE_RE.search(content)
    return m.group(1) if m else None


def is_dynamic_context_reminder(message: object) -> bool:
    """yyds: 判断消息是否是本中间件注入的便签（通过 additional_kwargs 标记识别）。"""
    return isinstance(message, HumanMessage) and bool(message.additional_kwargs.get(_DYNAMIC_CONTEXT_REMINDER_KEY))


def _last_injected_date(messages: list) -> str | None:
    """yyds: 反向扫描消息列表，找到最近一次注入的日期（用于跨午夜检测）。

    yyds 执行顺序：
      ① 反向遍历 messages（从最新到最老）
      ② 找到第一条 is_dynamic_context_reminder 的消息
      ③ 从其内容中提取 <current_date> 值并返回
      ④ 全部扫完没找到 → 返回 None（说明是首轮对话）
    """
    for msg in reversed(messages):
        if is_dynamic_context_reminder(msg):
            content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
            return _extract_date(content_str)
    return None


def _is_user_injection_target(message: object) -> bool:
    """yyds: 判断消息是否可以接收动态上下文注入 — 排除便签和摘要消息。

    排除规则：
      - is_dynamic_context_reminder → 本中间件注入的，不能重复注入
      - name == "summary" → summarization 生成的摘要，不是用户发的，不应注入
    """
    return isinstance(message, HumanMessage) and not is_dynamic_context_reminder(message) and message.name != _SUMMARY_MESSAGE_NAME


class DynamicContextMiddleware(AgentMiddleware):
    """yyds: 动态上下文注入中间件 — 在用户消息前悄悄塞一条便签。

    执行时机：before_agent（Agent 执行前，不是 wrap_tool_call/wrap_model_call）。
    操作模式：修改 state["messages"]，在用户消息前面插入便签。

    为什么注入到 HumanMessage 而不是 SystemMessage？
      system prompt 是固定的，所有用户共享同一份。LLM 供应商会缓存它（省钱 90%）。
      如果每个用户的记忆都写进 system prompt，缓存就失效了（每个人不一样）。
      所以动态内容塞到 HumanMessage 里，system prompt 保持不变。

    三种情况：
      首轮（没注入过）→ 插完整便签（记忆 + 日期）
      同一天（上次日期 == 今天）→ 不插
      跨午夜（上次日期 ≠ 今天）→ 插轻量便签（仅日期）

    ID 交换技巧（核心设计）：

      LangGraph 更新消息的规则：
        新消息的 id 和已有消息相同 → 原地替换那条消息
        新消息的 id 是全新的       → 追加到列表末尾

      所以要"在用户消息前面插一条便签"，不能直接 append（会跑到末尾），
      而是用 ID 交换：

        原始消息：
          [SystemMsg] [AIMsg] [HumanMsg id="h1" "今天天气怎么样？"]

        中间件返回：
          reminder_msg: id="h1", content="便签：今天是2025-05-13"    ← 同 id，替换原始
          user_msg:     id="h1__user", content="今天天气怎么样？"    ← 新 id，追加

        LangGraph 处理后：
          [SystemMsg] [AIMsg] [HumanMsg id="h1" "便签"] [HumanMsg id="h1__user" "今天天气怎么样？"]
                              ↑ 替换了原始消息            ↑ 追加在后面

      效果：便签在用户消息前面，原始消息消失，不重复。
    """

    def __init__(self, agent_name: str | None = None, *, app_config: AppConfig | None = None):
        super().__init__()
        self._agent_name = agent_name
        self._app_config = app_config

    def _build_full_reminder(self) -> str:
        """yyds: 构建完整便签（记忆 + 日期）— 首轮注入时使用。

        yyds 执行顺序：
          ① 检查 memory.injection_enabled 是否开启记忆注入
          ② 开启则调用 _get_memory_context() 获取用户记忆文本
          ③ 获取当前日期（格式：2025-05-13, Tuesday）
          ④ 拼装 <system-reminder>...<current_date>...</system-reminder>
        """
        from deerflow.agents.lead_agent.prompt import _get_memory_context

        # yyds: ①② 获取记忆上下文（受 injection_enabled 控制）
        injection_enabled = self._app_config.memory.injection_enabled if self._app_config else True
        memory_context = _get_memory_context(self._agent_name, app_config=self._app_config) if injection_enabled else ""
        # yyds: ③ 获取当前日期
        current_date = datetime.now().strftime("%Y-%m-%d, %A")

        # yyds: ④ 拼装完整便签（记忆 + 空行分隔 + 日期）
        lines: list[str] = ["<system-reminder>"]
        if memory_context:
            lines.append(memory_context.strip())
            lines.append("")  # yyds: 空行分隔记忆和日期，方便 LLM 区分
        lines.append(f"<current_date>{current_date}</current_date>")
        lines.append("</system-reminder>")

        return "\n".join(lines)

    def _build_date_update_reminder(self) -> str:
        """yyds: 构建轻量日期更新便签（跨午夜时使用，不含记忆）。

        为什么跨午夜不注入记忆？
          记忆在首轮已经注入并冻结了，后续轮次不应该重复注入。
          跨午夜只需要告诉 LLM "日期变了"就够了。
        """
        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        return "\n".join(
            [
                "<system-reminder>",
                f"<current_date>{current_date}</current_date>",
                "</system-reminder>",
            ]
        )

    @staticmethod
    def _make_reminder_and_user_messages(original: HumanMessage, reminder_content: str) -> tuple[HumanMessage, HumanMessage]:
        """yyds: ID 交换 — 生成 (便签消息, 用户消息) 对。

        yyds 执行顺序：
          ① 获取原始消息的 id（没有则生成 uuid）
          ② 便签消息：id = 原始 id → LangGraph 会替换原始消息
             - hide_from_ui=True → 前端不展示
             - _DYNAMIC_CONTEXT_REMINDER_KEY=True → 标记为本中间件注入
          ③ 用户消息：id = "{原始id}__user" → LangGraph 会追加到便签后面
             - 保留原始 content/name/additional_kwargs
          ④ 返回 (便签, 用户消息) — 由调用者写入 state

        举例：
          原始: HumanMessage(id="h1", content="今天天气怎么样？")
          返回: (HumanMessage(id="h1", content="便签..."),
                 HumanMessage(id="h1__user", content="今天天气怎么样？"))
          LangGraph 处理: id="h1" 替换原始, id="h1__user" 追加 → 便签在用户消息前
        """
        # yyds: ① 获取原始消息的稳定 id
        stable_id = original.id or str(uuid.uuid4())
        # yyds: ② 便签消息（继承原始 id → 替换原始消息）
        reminder_msg = HumanMessage(
            content=reminder_content,
            id=stable_id,
            additional_kwargs={"hide_from_ui": True, _DYNAMIC_CONTEXT_REMINDER_KEY: True},
        )
        # yyds: ③ 用户消息（派生 id → 追加到便签后面）
        user_msg = HumanMessage(
            content=original.content,
            id=f"{stable_id}__user",
            name=original.name,
            additional_kwargs=original.additional_kwargs,
        )
        # yyds: ④ 返回消息对
        return reminder_msg, user_msg

    def _inject(self, state) -> dict | None:
        """yyds: 核心注入逻辑 — 根据历史注入状态决定注入什么。

        yyds 执行顺序：
          ① 从 state 获取消息列表，空列表则跳过
          ② 计算当前日期
          ③ _last_injected_date() 反向扫描找到最近注入的日期 → last_date
          ④ 首轮（last_date is None）→ 找第一条可注入消息 → 注入完整便签（记忆+日期）
          ⑤ 同一天（last_date == current_date）→ 不注入，返回 None
          ⑥ 跨午夜（last_date != current_date）→ 找最后一条可注入消息 → 注入轻量日期更新
        """
        # yyds: ① 获取消息列表
        messages = list(state.get("messages", []))
        if not messages:
            return None

        # yyds: ②③ 计算当前日期 + 查找最近注入的日期
        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        last_date = _last_injected_date(messages)
        logger.debug(
            "DynamicContextMiddleware._inject: msg_count=%d last_date=%r current_date=%r",
            len(messages),
            last_date,
            current_date,
        )

        # yyds: ④ 首轮 — 注入完整便签（记忆 + 日期）
        if last_date is None:
            first_idx = next((i for i, m in enumerate(messages) if _is_user_injection_target(m)), None)
            if first_idx is None:
                return None
            full_reminder = self._build_full_reminder()
            logger.info(
                "DynamicContextMiddleware: injecting full reminder (len=%d, has_memory=%s) into first HumanMessage id=%r",
                len(full_reminder),
                "<memory>" in full_reminder,
                messages[first_idx].id,
            )
            reminder_msg, user_msg = self._make_reminder_and_user_messages(messages[first_idx], full_reminder)
            return {"messages": [reminder_msg, user_msg]}

        # yyds: ⑤ 同一天 — 不注入
        if last_date == current_date:
            return None

        # yyds: ⑥ 跨午夜 — 注入轻量日期更新（不含记忆）
        last_human_idx = next((i for i in reversed(range(len(messages))) if _is_user_injection_target(messages[i])), None)
        if last_human_idx is None:
            return None

        reminder_msg, user_msg = self._make_reminder_and_user_messages(messages[last_human_idx], self._build_date_update_reminder())
        logger.info("DynamicContextMiddleware: midnight crossing detected — injected date update before current turn")
        return {"messages": [reminder_msg, user_msg]}

    @override
    def before_agent(self, state, runtime: Runtime) -> dict | None:
        """yyds: 同步版 — 直接调用 _inject。"""
        return self._inject(state)

    @override
    async def abefore_agent(self, state, runtime: Runtime) -> dict | None:
        """yyds: 异步版 — 逻辑和同步版完全相同（_inject 内部无异步操作）。"""
        return self._inject(state)
