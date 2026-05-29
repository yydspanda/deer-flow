"""yyds: 标题生成中间件 — 用户第一次对话后，自动给会话起个名字。

【做什么】用户第一次发消息、Agent 回复后，根据对话内容自动生成一个简短标题。
   生成后写入 state["title"]，前端在会话列表里显示这个标题。
【为什么存在】用户创建新会话时没有标题。没有标题的话，会话列表里全是"New Conversation"，
   用户分不清哪个是哪个。自动生成标题让用户一眼看出每个会话在聊什么。
【在链中的位置】after_model 阶段（模型返回响应后触发），在 agent.py 的 _build_middlewares 第 ⑤ 步追加。
【关键设计】
   - 触发条件：恰好 1 条用户消息 + 至少 1 条 AI 回复 + 还没有标题（只生成一次）
   - 同步版（after_model）：本地截取策略，直接取用户消息前 50 字符加 "..."
   - 异步版（aafter_model）：调用 LLM 生成高质量标题，失败时回退到本地策略
   - 为什么同步版不调 LLM？同步调用 LLM 会阻塞整个 Agent 运行，太慢了。
     所以同步版用最快的本地截取，异步版才调 LLM 生成更好的标题。
   - tag "middleware:title"：让日志系统能区分"主 Agent 的 LLM 调用"和"标题生成的 LLM 调用"
   - 去除 <think/> 标签：推理模型（DeepSeek-R1 等）的输出含思考过程，不能当标题
"""

import logging
import re
from typing import TYPE_CHECKING, Any, NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.dynamic_context_middleware import is_dynamic_context_reminder
from deerflow.config.title_config import get_title_config
from deerflow.models import create_chat_model

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig
    from deerflow.config.title_config import TitleConfig

logger = logging.getLogger(__name__)

_THINK_TAG_RE = re.compile(r"<think[\s\S]*?</think\s*>", re.IGNORECASE)  # yyds: 匹配推理模型的 <think/> 标签（含属性）


class TitleMiddlewareState(AgentState):
    """yyds: Title 中间件的状态扩展 — 在 AgentState 基础上加了 title 字段。

    state["title"] 为 None → 还没生成标题
    state["title"] 为 str → 已有标题，不再重复生成
    """

    title: NotRequired[str | None]


class TitleMiddleware(AgentMiddleware[TitleMiddlewareState]):
    """yyds: 标题生成中间件 — 用户第一次对话后，自动给会话起个名字。

    执行时机：after_model（模型返回响应后）。
    触发条件：恰好 1 条用户消息 + 至少 1 条 AI 回复 + state["title"] 为空。
    只触发一次——生成后 state["title"] 有值，后续轮次不再触发。

    同步 vs 异步的区别：
      同步版（after_model）：用本地策略（截取用户消息前 50 字符），不调 LLM，快。
      异步版（aafter_model）：调 LLM 生成高质量标题，失败时回退到本地策略。
      为什么？同步调 LLM 会阻塞整个 Agent 运行。DeerFlow 的 make dev 走异步路径，
      所以生产环境用的是异步版的 LLM 标题。

    数据流：
      after_model / aafter_model
        └─ _should_generate_title()
             ├─ title 已存在 → 返回 None（跳过）
             ├─ 不是首次交互 → 返回 None（跳过）
             └─ 是首次交互 → 继续
                  ├─ 同步：_build_title_prompt() → _fallback_title()
                  │         截取用户消息前 50 字符 → {"title": "今天天气怎么样？..."}
                  └─ 异步：_build_title_prompt() → LLM 生成
                            ├─ 成功 → _parse_title() → {"title": "北京天气查询"}
                            └─ 失败 → _fallback_title() → {"title": "今天天气怎么样？..."}

    LLM 标题生成的 prompt 长什么样？
      默认模板（title_config.py）：
        "Generate a concise title (max 6 words) for this conversation.
         User: 今天天气怎么样？
         Assistant: 北京今天晴天，25°C...
         Return ONLY the title, no quotes, no explanation."
      LLM 返回："北京天气查询"
    """

    state_schema = TitleMiddlewareState

    def __init__(self, *, app_config: "AppConfig | None" = None, title_config: "TitleConfig | None" = None):
        super().__init__()
        self._app_config = app_config
        self._title_config = title_config

    def _get_title_config(self):
        """yyds: 获取标题配置 — 优先用构造时传入的，其次从 app_config 取，最后用全局默认。"""
        if self._title_config is not None:
            return self._title_config
        if self._app_config is not None:
            return self._app_config.title
        return get_title_config()

    def _normalize_content(self, content: object) -> str:
        """yyds: 把消息内容统一转成字符串 — 处理三种 content 格式。

        yyds 执行顺序：
          ① str → 直接返回（最常见）
          ② list（Anthropic 的 content blocks 数组）→ 递归处理每个元素，用换行拼接
          ③ dict → 先尝试取 "text" 键，再尝试取 "content" 键（嵌套结构）
          ④ 其他类型 → 返回空字符串

        为什么需要这个函数？
          不同 Provider 的消息格式不一样：
          OpenAI:  content = "你好"                    → str
          Anthropic: content = [{"type":"text","text":"你好"}]  → list
          混合:    content = {"text": "你好"}          → dict
          统一转成 str，后面的逻辑不用管格式差异。
        """
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = [self._normalize_content(item) for item in content]
            return "\n".join(part for part in parts if part)

        if isinstance(content, dict):
            text_value = content.get("text")
            if isinstance(text_value, str):
                return text_value

            nested_content = content.get("content")
            if nested_content is not None:
                return self._normalize_content(nested_content)

        return ""

    @staticmethod
    def _is_user_message_for_title(message: object) -> bool:
        """yyds: 判断消息是否是真正的用户消息 — 排除 DynamicContextMiddleware 注入的便签。

        为什么要排除？
          DynamicContextMiddleware 会在第一条用户消息前插入一条 HumanMessage（便签），
          内容是 "<system-reminder>...<current_date>...</system-reminder>"。
          如果不排除，这条便签会被当作用户消息，导致标题变成日期信息。
        """
        return getattr(message, "type", None) == "human" and not is_dynamic_context_reminder(message)

    def _should_generate_title(self, state: TitleMiddlewareState) -> bool:
        """yyds: 判断是否需要生成标题 — 三个条件全满足才生成。

        yyds 执行顺序：
          ① title 配置未启用 → 不生成
          ② state["title"] 已有值 → 不生成（已经生成过了）
          ③ 消息数 < 2 → 不生成（至少要有一问一答）
          ④ 用户消息数 ≠ 1 → 不生成（只在首次交互时生成）
          ⑤ AI 回复数 < 1 → 不生成（Agent 还没回复）
          ⑥ 全部通过 → 返回 True
        """
        config = self._get_title_config()
        # yyds: ① 配置检查
        if not config.enabled:
            return False

        # yyds: ② 已有标题，不重复生成
        if state.get("title"):
            return False

        # yyds: ③ 消息数检查
        messages = state.get("messages", [])
        if len(messages) < 2:
            return False

        # yyds: ④⑤⑥ 统计用户消息和 AI 回复数量
        user_messages = [m for m in messages if self._is_user_message_for_title(m)]
        assistant_messages = [m for m in messages if m.type == "ai"]

        return len(user_messages) == 1 and len(assistant_messages) >= 1

    def _build_title_prompt(self, state: TitleMiddlewareState) -> tuple[str, str]:
        """yyds: 构建标题生成 prompt — 从对话中提取用户消息和 AI 回复，填入模板。

        yyds 执行顺序：
          ① 从 messages 中提取第一条用户消息和第一条 AI 回复的内容
          ② _normalize_content 统一转字符串
          ③ _strip_think_tags 去除推理模型的 <think/> 标签（AI 回复可能含思考过程）
          ④ 内容截断到 500 字符（防止 prompt 太长，标题生成不需要完整内容）
          ⑤ 填入 config.prompt_template，返回 (prompt, user_msg)

        返回 (prompt, user_msg)：
          prompt — 发给 LLM 的完整 prompt
          user_msg — 原始用户消息文本（用于 LLM 失败时的本地回退）
        """
        config = self._get_title_config()
        messages = state.get("messages", [])

        # yyds: ① 提取内容
        user_msg_content = next((m.content for m in messages if self._is_user_message_for_title(m)), "")
        assistant_msg_content = next((m.content for m in messages if m.type == "ai"), "")

        # yyds: ②③ 统一格式 + 去除 <think/> 标签
        user_msg = self._normalize_content(user_msg_content)
        assistant_msg = self._strip_think_tags(self._normalize_content(assistant_msg_content))

        # yyds: ④⑤ 截断 + 填入模板
        prompt = config.prompt_template.format(
            max_words=config.max_words,
            user_msg=user_msg[:500],
            assistant_msg=assistant_msg[:500],
        )
        return prompt, user_msg

    def _strip_think_tags(self, text: str) -> str:
        """yyds: 去除推理模型的 <think/> 标签 — DeepSeek-R1、minimax 等模型会输出思考过程。

        例如："<think\n>让我分析一下用户的问题...</think\n>北京今天晴天，25°C"
        → "北京今天晴天，25°C"

        如果不去除，标题可能变成"让我分析一下用户的问题"，而不是真正的回复内容。
        """
        return _THINK_TAG_RE.sub("", text).strip()

    def _parse_title(self, content: object) -> str:
        """yyds: 清理 LLM 输出 → 干净的标题字符串。

        yyds 执行顺序：
          ① _normalize_content 统一转字符串
          ② _strip_think_tags 去除 <think/> 标签（推理模型可能还在输出里带思考过程）
          ③ strip() 去首尾空白 + strip('"') strip("'") 去引号
             （LLM 经常返回 "北京天气查询" 而不是 北京天气查询）
          ④ 截断到 max_chars（默认 60 字符）
        """
        config = self._get_title_config()
        # yyds: ①②③ 清理
        title_content = self._normalize_content(content)
        title_content = self._strip_think_tags(title_content)
        title = title_content.strip().strip('"').strip("'")
        # yyds: ④ 截断
        return title[: config.max_chars] if len(title) > config.max_chars else title

    def _fallback_title(self, user_msg: str) -> str:
        """yyds: 本地回退标题 — 截取用户消息前 50 字符 + "..."。

        什么时候用这个？
          1. 同步版（after_model）— 不调 LLM，直接用这个
          2. 异步版 LLM 调用失败 — 降级到这个
        """
        config = self._get_title_config()
        fallback_chars = min(config.max_chars, 50)
        if len(user_msg) > fallback_chars:
            return user_msg[:fallback_chars].rstrip() + "..."
        return user_msg if user_msg else "New Conversation"

    def _get_runnable_config(self) -> dict[str, Any]:
        """yyds: 构建 LLM 调用配置 — 继承父配置 + 添加 tag "middleware:title"。

        yyds 执行顺序：
          ① get_config() 获取当前运行的 RunnableConfig（含 thread_id、run_id 等）
          ② 复制一份（不修改父配置）
          ③ run_name = "title_agent"（日志里能看到是标题生成）
          ④ tags 追加 "middleware:title"（RunJournal 能区分标题生成 vs 主 Agent 调用）
        """
        try:
            parent = get_config()
        except Exception:
            parent = {}
        config = {**parent}
        config["run_name"] = "title_agent"
        config["tags"] = [*(config.get("tags") or []), "middleware:title"]
        return config

    def _generate_title_result(self, state: TitleMiddlewareState) -> dict | None:
        """yyds: 同步版标题生成 — 本地截取策略（不调 LLM，最快）。

        yyds 执行顺序：
          ① _should_generate_title() 检查是否需要生成
          ② 不需要 → 返回 None
          ③ _build_title_prompt() 提取对话内容
          ④ _fallback_title() 截取用户消息前 50 字符
          ⑤ 返回 {"title": "今天天气怎么样？..."}

        为什么同步版不调 LLM？
          同步调用 LLM 会阻塞整个 Agent 运行（等网络请求回来才能继续）。
          标题生成不是关键路径（用户不会因为标题慢了几百毫秒就觉得不好），
          所以同步版用最快的本地截取，异步版才调 LLM。
        """
        # yyds: ①② 检查是否需要生成
        if not self._should_generate_title(state):
            return None

        # yyds: ③④ 本地截取
        _, user_msg = self._build_title_prompt(state)
        return {"title": self._fallback_title(user_msg)}

    async def _agenerate_title_result(self, state: TitleMiddlewareState) -> dict | None:
        """yyds: 异步版标题生成 — 调 LLM 生成高质量标题，失败时回退到本地策略。

        yyds 执行顺序：
          ① _should_generate_title() 检查是否需要生成
          ② 不需要 → 返回 None
          ③ _build_title_prompt() 构建标题生成 prompt
          ④ create_chat_model() 创建 LLM 实例（thinking_enabled=False，不需要思考）
          ⑤ model.ainvoke(prompt) 异步调用 LLM
          ⑥ _parse_title() 清理输出 → 返回 {"title": "北京天气查询"}
          ⑦ 如果步骤 ④⑤⑥ 任一步失败 → _fallback_title() 回退到本地截取

        为什么 thinking_enabled=False？
          标题生成不需要推理，关掉 thinking 省钱省时间。
        """
        # yyds: ①② 检查是否需要生成
        if not self._should_generate_title(state):
            return None

        config = self._get_title_config()
        # yyds: ③ 构建标题生成 prompt
        prompt, user_msg = self._build_title_prompt(state)

        try:
            # yyds: ④ 创建 LLM 实例（用配置的 model_name 或默认模型，关闭 thinking）
            # attach_tracing=False because ``_get_runnable_config()`` inherits
            # the graph-level RunnableConfig (set in ``_make_lead_agent``) whose
            # callbacks already carry tracing handlers; binding them again at
            # the model level would emit duplicate spans.
            model_kwargs = {"thinking_enabled": False, "attach_tracing": False}
            if self._app_config is not None:
                model_kwargs["app_config"] = self._app_config
            if config.model_name:
                model = create_chat_model(name=config.model_name, **model_kwargs)
            else:
                model = create_chat_model(**model_kwargs)
            # yyds: ⑤ 异步调用 LLM
            response = await model.ainvoke(prompt, config=self._get_runnable_config())
            # yyds: ⑥ 清理输出
            title = self._parse_title(response.content)
            if title:
                return {"title": title}
        except Exception:
            logger.debug("Failed to generate async title; falling back to local title", exc_info=True)
        # yyds: ⑦ LLM 失败 → 回退到本地截取
        return {"title": self._fallback_title(user_msg)}

    @override
    def after_model(self, state: TitleMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 同步版 — 本地截取策略。"""
        return self._generate_title_result(state)

    @override
    async def aafter_model(self, state: TitleMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 异步版 — LLM 生成 + 本地回退。"""
        return await self._agenerate_title_result(state)
