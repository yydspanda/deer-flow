"""yyds: 模型工厂 — 给一个配置名，返回一个能用的 ChatModel 实例。

【大白话讲清楚】
  Agent 要调 LLM，需要先创建一个模型实例（ChatOpenAI、ChatAnthropic 等）。
  但"用哪个模型、用什么参数"写在 config.yaml 里，不是硬编码的。
  这个工厂函数就是翻译官：读配置 → 找到模型类 → 拼参数 → 实例化 → 返回。

  最复杂的部分是**思维模式切换**：
    thinking_enabled=True  → 模型启用了深度思考（如 Claude Extended Thinking）
    thinking_enabled=False → 正常模式
    config.yaml 里的 when_thinking_enabled / when_thinking_disabled 控制两种模式的参数差异。

【具体例子】
  config.yaml:
    models:
      - name: claude-sonnet-4
        use: langchain_anthropic:ChatAnthropic
        model: claude-sonnet-4-20250514
        thinking:
          type: enabled
          budget_tokens: 10000
        when_thinking_disabled:
          thinking:
            type: disabled

  调用 create_chat_model("claude-sonnet-4", thinking_enabled=True):
    → resolve_class("langchain_anthropic:ChatAnthropic") → 拿到类
    → 合并参数：model="claude-sonnet-4-20250514" + thinking={"type":"enabled","budget_tokens":10000}
    → ChatAnthropic(model="claude-sonnet-4-20250514", thinking={"type":"enabled","budget_tokens":10000})

  调用 create_chat_model("claude-sonnet-4", thinking_enabled=False):
    → 同上，但用 when_thinking_disabled 的参数：thinking={"type":"disabled"}

【在链中的位置】
  make_lead_agent() → create_chat_model(name, thinking_enabled=...)
  Sub-Agent 执行时 → create_chat_model(name)
  MemoryUpdater → create_chat_model(name)  ← 用便宜模型做记忆提取
  TitleMiddleware → create_chat_model(name)

---
Model factory — creates ChatModel instances from config.
"""

import logging

from langchain.chat_models import BaseChatModel

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.reflection import resolve_class
from deerflow.tracing import build_tracing_callbacks

logger = logging.getLogger(__name__)


def _deep_merge_dicts(base: dict | None, override: dict) -> dict:
    """yyds: 递归合并两个字典 — override 覆盖 base，嵌套 dict 递归合并不覆盖。

    例：base={"thinking": {"type": "enabled"}} + override={"thinking": {"budget": 1000}}
    → {"thinking": {"type": "enabled", "budget": 1000}}（不是 {"thinking": {"budget": 1000}}）
    """
    merged = dict(base or {})
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _vllm_disable_chat_template_kwargs(chat_template_kwargs: dict) -> dict:
    """yyds: 构建禁用思维的参数 — 把 thinking/enable_thinking 设为 False。

    vLLM 部署的 Qwen 等模型通过 chat_template_kwargs 控制思维模式：
      thinking=True  → 启用
      thinking=False → 禁用
    这个函数构造 {"thinking": False} 这样的参数，传给 vLLM 关闭思维。
    """
    disable_kwargs: dict[str, bool] = {}
    if "thinking" in chat_template_kwargs:
        disable_kwargs["thinking"] = False
    if "enable_thinking" in chat_template_kwargs:
        disable_kwargs["enable_thinking"] = False
    return disable_kwargs


def _enable_stream_usage_by_default(model_use_path: str, model_settings_from_config: dict) -> None:
    """yyds: 为 OpenAI 兼容模型自动启用 stream_usage。

    为什么？LangChain 只对原生 OpenAI 自动启用 stream_usage（用于追踪 token 用量）。
    DeerFlow 经常用 OpenAI 兼容网关（如豆包、DeepSeek），base_url 不是官方的，
    LangChain 不会自动启用 → TokenUsageMiddleware 拿不到 token 数据。
    所以这里手动补上。
    """
    if model_use_path != "langchain_openai:ChatOpenAI":
        return
    if "stream_usage" in model_settings_from_config:
        return
    if "base_url" in model_settings_from_config or "openai_api_base" in model_settings_from_config:
        model_settings_from_config["stream_usage"] = True




# Default chunk-gap budget for OpenAI-compatible streaming responses.
#
# langchain-openai raises ``StreamChunkTimeoutError`` after this many seconds
# without receiving a chunk. Its own default is 60s, which is too aggressive for
# reasoning models (DeepSeek-R1, Doubao-thinking, GPT-5) whose first chunk can
# legitimately take 90~150s. We default to 240s so the streaming layer rarely
# trips on long thinking pauses; the LLMErrorHandlingMiddleware still retries
# (budget=2) if a real stall happens. Users can override per-model in config.yaml.
_DEFAULT_STREAM_CHUNK_TIMEOUT_SECONDS: float = 240.0


def _apply_stream_chunk_timeout_default(model_use_path: str, model_settings_from_config: dict) -> None:
    """Inject a generous ``stream_chunk_timeout`` for OpenAI-compatible clients.

    The ``stream_chunk_timeout`` kwarg is specific to ``langchain_openai:ChatOpenAI``
    and is rejected by other providers' constructors as an unexpected keyword
    argument. Behaviour:

    * OpenAI-compatible path: an explicit value in ``config.yaml`` is preserved.
      An explicit ``null`` is dropped upstream by ``model_dump(exclude_none=True)``
      and therefore treated as "unset", so the default is injected.
    * Non-OpenAI path: drop the key so it is never forwarded to an incompatible
      constructor (which would raise ``TypeError: unexpected keyword argument``).
    """
    if model_use_path != "langchain_openai:ChatOpenAI":
        model_settings_from_config.pop("stream_chunk_timeout", None)
        return
    if "stream_chunk_timeout" in model_settings_from_config:
        return
    model_settings_from_config["stream_chunk_timeout"] = _DEFAULT_STREAM_CHUNK_TIMEOUT_SECONDS
# yyds: 核心工厂函数，根据模型名称和思维模式配置创建 ChatModel 实例


def create_chat_model(name: str | None = None, thinking_enabled: bool = False, *, app_config: AppConfig | None = None, attach_tracing: bool = True, **kwargs) -> BaseChatModel:
    """yyds: 核心工厂函数 — 给个名字，返回一个 ChatModel 实例。

    完整流程：

    给个名字（如 "gpt-4o"）
      │
      │ ① 找配置
      │    config.yaml 里 models 列表 → 找到 name 匹配的 ModelConfig
      │
      │ ② 找类
      │    resolve_class("langchain_openai:ChatOpenAI") → 拿到 Python 类
      │
      │ ③ 拼参数
      │    ModelConfig 里除了 name/use 等元数据，其余都是模型参数
      │    根据 thinking_enabled 合并/覆盖思维模式参数
      │
      │ ④ 实例化
      │    ChatOpenAI(model="gpt-4o", api_key=..., stream_usage=True, ...)
      │
      │ ⑤ 挂 tracing
      │    如果 attach_tracing=True → 绑定 Langfuse/LangSmith 回调
      │
      └─ 返回模型实例

    参数：
      name — config.yaml 里配的模型名。None → 用第一个模型
      thinking_enabled — 是否启用深度思考模式
      app_config — 配置对象，默认用全局单例
      attach_tracing — 是否挂 tracing 回调。MemoryUpdater 等独立调用者保持 True，
                       make_lead_agent 等已在 graph 层挂了 tracing 的传 False（避免重复）

    ---
    Create a chat model instance from the config.

    Args:
        name: The name of the model to create. If None, the first model in the config will be used.
        thinking_enabled: Enable the model's extended-thinking mode when supported.
        app_config: Explicit application config; falls back to the cached global if omitted.
        attach_tracing: When True (default), attach tracing callbacks (Langfuse,
            LangSmith) directly to the model instance.
    """
    # ① 找配置 — 从 config.yaml 的 models 列表里找到 name 匹配的那一项
    config = app_config or get_app_config()
    if name is None:
        name = config.models[0].name
    model_config = config.get_model_config(name)
    if model_config is None:
        raise ValueError(f"Model {name} not found in config") from None

    # ② 找类 — resolve_class 把 "langchain_openai:ChatOpenAI" 解析成实际的 Python 类
    model_class = resolve_class(model_config.use, BaseChatModel)

    # ③ 拼参数 — ModelConfig 里除了元数据（name/use/display_name 等），其余都是模型构造参数
    model_settings_from_config = model_config.model_dump(
        exclude_none=True,
        exclude={
            "use",
            "name",
            "display_name",
            "description",
            "supports_thinking",
            "supports_reasoning_effort",
            "when_thinking_enabled",
            "when_thinking_disabled",
            "thinking",
            "supports_vision",
        },
    )
    # ③b 思维模式参数合并 — thinking 快捷字段 + when_thinking_enabled 完整字段
    # config.yaml 里的 thinking: {type: enabled, budget_tokens: 10000} 是快捷写法
    # when_thinking_enabled: {thinking: {type: enabled}, max_tokens: 8000} 是完整写法
    # 两者合并，完整写法优先
    has_thinking_settings = (model_config.when_thinking_enabled is not None) or (model_config.thinking is not None)
    effective_wte: dict = dict(model_config.when_thinking_enabled) if model_config.when_thinking_enabled else {}
    if model_config.thinking is not None:
        merged_thinking = {**(effective_wte.get("thinking") or {}), **model_config.thinking}
        effective_wte = {**effective_wte, "thinking": merged_thinking}
    if thinking_enabled and has_thinking_settings:
        # 启用思维模式：合并 when_thinking_enabled 的参数
        if not model_config.supports_thinking:
            raise ValueError(f"Model {name} does not support thinking. Set `supports_thinking` to true in the `config.yaml` to enable thinking.") from None
        if effective_wte:
            model_settings_from_config.update(effective_wte)
    if not thinking_enabled:
        # 禁用思维模式：四种不同的禁用策略，适配不同模型
        if model_config.when_thinking_disabled is not None:
            # yyds: 策略 1 — 用户显式配了 when_thinking_disabled，直接用
            model_settings_from_config.update(model_config.when_thinking_disabled)
        elif has_thinking_settings and effective_wte.get("extra_body", {}).get("thinking", {}).get("type"):
            # yyds: 策略 2 — OpenAI 兼容网关（如豆包），thinking 嵌在 extra_body 里
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"thinking": {"type": "disabled"}},
            )
            model_settings_from_config["reasoning_effort"] = "minimal"
        elif has_thinking_settings and (disable_chat_template_kwargs := _vllm_disable_chat_template_kwargs(effective_wte.get("extra_body", {}).get("chat_template_kwargs") or {})):
            # yyds: 策略 3 — vLLM 部署的 Qwen，通过 chat_template_kwargs 控制
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"chat_template_kwargs": disable_chat_template_kwargs},
            )
        elif has_thinking_settings and effective_wte.get("thinking", {}).get("type"):
            # yyds: 策略 4 — 原生 langchain_anthropic，thinking 是直接构造参数
            model_settings_from_config["thinking"] = {"type": "disabled"}
    # yyds: 不支持 reasoning_effort 的模型，删掉这个参数（否则构造函数报错）
    if not model_config.supports_reasoning_effort:
        kwargs.pop("reasoning_effort", None)
        model_settings_from_config.pop("reasoning_effort", None)

    # yyds: 自动补 stream_usage（OpenAI 兼容网关需要）
    _enable_stream_usage_by_default(model_config.use, model_settings_from_config)
    _apply_stream_chunk_timeout_default(model_config.use, model_settings_from_config)

    # yyds: Codex Responses API 特殊处理 — 用 reasoning_effort 代替 thinking
    from deerflow.models.openai_codex_provider import CodexChatModel

    if issubclass(model_class, CodexChatModel):
        model_settings_from_config.pop("max_tokens", None)
        explicit_effort = kwargs.pop("reasoning_effort", None)
        if not thinking_enabled:
            model_settings_from_config["reasoning_effort"] = "none"
        elif explicit_effort and explicit_effort in ("low", "medium", "high", "xhigh"):
            model_settings_from_config["reasoning_effort"] = explicit_effort
        elif "reasoning_effort" not in model_settings_from_config:
            model_settings_from_config["reasoning_effort"] = "medium"

    # yyds: 华为昇腾 MindIE 特殊处理 — 限制重试次数，防止级联超时
    if getattr(model_class, "__name__", "") == "MindIEChatModel":
        model_settings_from_config["max_retries"] = model_settings_from_config.get("max_retries", 1)

    # yyds: 兜底补 stream_usage — 只要模型类支持这个字段就默认开启
    if "stream_usage" not in model_settings_from_config and "stream_usage" not in kwargs:
        if "stream_usage" in getattr(model_class, "model_fields", {}):
            model_settings_from_config["stream_usage"] = True

    # ④ 实例化 — 把所有参数传给模型类构造函数
    model_instance = model_class(**kwargs, **model_settings_from_config)

    # ⑤ 挂 tracing — Langfuse/LangSmith 回调，用于记录 LLM 调用日志
    if attach_tracing:
        callbacks = build_tracing_callbacks()
        if callbacks:
            existing_callbacks = model_instance.callbacks or []
            model_instance.callbacks = [*existing_callbacks, *callbacks]
            logger.debug(f"Tracing attached to model '{name}' with providers={len(callbacks)}")
    return model_instance
