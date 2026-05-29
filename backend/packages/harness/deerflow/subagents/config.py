"""yyds: Sub-Agent 配置定义 — 每个 sub-agent 的"身份证"。

【大白话讲清楚】
  每个 sub-agent 启动前需要知道：用什么模型、能用哪些工具、超时多久、最多跑几轮。
  SubagentConfig 就是这张"身份证"——一个纯数据类，不包含任何逻辑。

  关键字段的设计意图：
    - tools=None：默认继承父 Agent 所有工具。设了白名单就只能用指定的。
    - disallowed_tools=["task"]：默认禁用 task，防止 sub-agent 再创建 sub-agent（递归爆炸）。
    - model="inherit"：默认用父 Agent 的模型。可以指定别的模型（比如用便宜的做简单任务）。
    - max_turns=50：防止 sub-agent 无限循环。50 轮大概够处理大多数任务。
    - timeout_seconds=900：15 分钟硬超时，sub-agent 挂了也不影响主对话。

  模型解析的优先级（resolve_subagent_model_name）：
    config.model != "inherit" → 用 config 指定的模型
    config.model == "inherit" → 用 parent_model（父 Agent 的模型）
    parent_model 也是 None   → 用 config.yaml 第一个模型（兜底）

【具体例子】
  内置 general-purpose sub-agent：
    SubagentConfig(
      name="general-purpose",
      tools=None,                    # 继承所有工具
      disallowed_tools=["task"],     # 但不能再创建 sub-agent
      model="inherit",               # 用父 Agent 同款模型
      max_turns=100,                 # 最多 100 轮
      timeout_seconds=900,           # 15 分钟超时
    )

  用户自定义的"代码审查"sub-agent（config.yaml 里定义）：
    SubagentConfig(
      name="code-reviewer",
      tools=["read_file", "bash"],   # 只给读文件和 bash
      disallowed_tools=["task"],     # 同样禁递归
      model="claude-sonnet",         # 指定模型，不用继承
      max_turns=30,                  # 审查不需要太多轮
      timeout_seconds=300,           # 5 分钟够了吧
    )

---
Subagent configuration definitions.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig


@dataclass
class SubagentConfig:
    """yyds: Sub-Agent 配置 — 纯数据类，不含逻辑。


    Attributes:
        name: Unique identifier for the subagent.
        description: When Claude should delegate to this subagent.
        system_prompt: The system prompt that guides the subagent's behavior.
        tools: Optional list of tool names to allow. If None, inherits all tools.
        disallowed_tools: Optional list of tool names to deny.
        skills: Optional list of skill names to load. If None, inherits all enabled skills.
                If an empty list, no skills are loaded.
        model: Model to use - 'inherit' uses parent's model.
        max_turns: Maximum agent turns before stopping. Built-in agents use the
            value set here (general-purpose=150, bash=60) unless the global
            ``subagents.max_turns`` is set.
        timeout_seconds: Bare fallback execution-time cap. For built-in agents the
            effective limit is the global ``subagents.timeout_seconds`` (default
            1800 = 30 min), layered on by the registry; this 900 only applies
            when no differing global value exists.
    字段分组：
      身份：name + description（告诉 task_tool 什么时候该用这个 sub-agent）
      行为：system_prompt + tools + disallowed_tools + skills（控制 sub-agent 能做什么）
      资源：model + max_turns + timeout_seconds（控制 sub-agent 的资源上限）

    """

    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = field(default_factory=lambda: ["task"])  # yyds: 默认禁 task，防递归
    skills: list[str] | None = None
    model: str = "inherit"  # yyds: "inherit" = 跟父 Agent 用同一个模型
    max_turns: int = 50
    timeout_seconds: int = 900  # yyds: 15 分钟，sub-agent 挂了也不影响主对话


def _default_model_name(app_config: "AppConfig") -> str:
    """yyds: 兜底 — 没有父模型 + model="inherit" 时，取 config.yaml 第一个模型。"""
    if not app_config.models:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")
    return app_config.models[0].name


def resolve_subagent_model_name(config: SubagentConfig, parent_model: str | None, *, app_config: "AppConfig | None" = None) -> str:
    """yyds: 三级优先级解析模型名。

    ① config.model != "inherit" → 显式指定，直接用
    ② parent_model 有值 → 继承父 Agent 的
    ③ 都没有 → 兜底到 config.yaml 第一个模型
    """
    if config.model != "inherit":
        return config.model

    if parent_model is not None:
        return parent_model

    if app_config is None:
        from deerflow.config import get_app_config

        app_config = get_app_config()
    return _default_model_name(app_config)
