"""yyds: Sub-Agent 注册表 — 管理"有哪些 sub-agent 可用"以及"配置怎么覆盖"。

【大白话讲清楚】
  task_tool 需要知道"目前有哪些 sub-agent 可以用"。
  这个注册表回答两个问题：
    ① 列出所有可用的 sub-agent（内置 + 用户自定义）
    ② 给定名称，返回最终生效的 SubagentConfig（含配置覆盖）

  配置覆盖的三层合并（类似 CSS 层叠）：
    第一层：内置 sub-agent（代码里定义的 general-purpose、bash）
    第二层：config.yaml custom_agents 段（用户自定义的 sub-agent）
    第三层：config.yaml agents 段的 per-agent override（按名称覆盖特定字段）

  覆盖规则的关键约束：
    - 内置 sub-agent 受全局默认值影响（timeout_seconds、max_turns）
    - 用户自定义 sub-agent 不受全局默认值影响（它们有自己的默认值）
    - per-agent override 对两者都生效

  可用性过滤：
    sandbox 不允许 host bash → bash sub-agent 从可见列表中隐藏

【具体例子】
  config.yaml 配置了：
    subagents:
      timeout_seconds: 600              # 全局默认超时 10 分钟
      custom_agents:
        code-reviewer:                   # 用户自定义 sub-agent
          description: "代码审查"
          timeout_seconds: 300           # 自己的超时：5 分钟
      agents:
        general-purpose:
          timeout_seconds: 1200          # per-agent override：20 分钟
        bash:
          model: "claude-sonnet"         # bash 用便宜的模型

  最终生效的配置：
    general-purpose: timeout=1200（全局 600 被覆盖了）
    bash: timeout=600（用全局默认）, model=claude-sonnet（被覆盖了）
    code-reviewer: timeout=300（自己的值，不受全局 600 影响）

【在链中的位置】
  task_tool 调用链：
    task_tool → get_available_subagent_names() → 确认 sub-agent 存在
    task_tool → get_subagent_config("general-purpose") → 获取最终配置
    task_tool → 创建 SubagentExecutor(config, tools, ...)

---
Subagent registry for managing available subagents.
"""

import logging
from dataclasses import replace
from typing import Any

from deerflow.sandbox.security import is_host_bash_allowed
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


def _resolve_subagents_app_config(app_config: Any | None = None):
    """yyds: 解析 subagents 子配置 — 延迟加载避免循环导入。

    为什么延迟？config 模块导入 registry，registry 又导入 config，会循环。
    所以只在需要时才 import。
    """
    if app_config is None:
        from deerflow.config.subagents_config import get_subagents_app_config

        return get_subagents_app_config()
    return getattr(app_config, "subagents", app_config)


def _build_custom_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    """yyds: 从 config.yaml custom_agents 段构建 SubagentConfig。

    只查 custom_agents，不查内置。
    找不到返回 None（调用方会 fallback 到内置）。
    """
    subagents_config = _resolve_subagents_app_config(app_config)
    custom = subagents_config.custom_agents.get(name)
    if custom is None:
        return None

    return SubagentConfig(
        name=name,
        description=custom.description,
        system_prompt=custom.system_prompt,
        tools=custom.tools,
        disallowed_tools=custom.disallowed_tools,
        skills=custom.skills,
        model=custom.model,
        max_turns=custom.max_turns,
        timeout_seconds=custom.timeout_seconds,
    )


def get_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    """yyds: 核心 — 按名称获取最终生效的 sub-agent 配置，三层合并。

    查找 + 合并流程：
      ① 找基础配置：
         内置 sub-agents 里找 → 找不到 → custom_agents 里找 → 还找不到 → return None

      ② 应用覆盖（只改需要改的字段）：
         per-agent override 优先级最高（agents 段按名称指定的）
         全局默认值只影响内置 sub-agent（不影响 custom_agents 自己的值）

      ③ 返回合并后的 SubagentConfig

    四个可覆盖字段：
      - timeout_seconds: per-agent override > 全局默认(仅 builtin) > config 自身值
      - max_turns: 同上
      - model: 只有 per-agent override，没有全局默认
      - skills: 只有 per-agent override，没有全局默认
    """
    config = BUILTIN_SUBAGENTS.get(name)  # yyds: 先查内置
    if config is None:
        config = _build_custom_subagent_config(name, app_config=app_config)  # yyds: 再查自定义
    if config is None:
        return None

    subagents_config = _resolve_subagents_app_config(app_config)
    is_builtin = name in BUILTIN_SUBAGENTS  # yyds: 区分内置和自定义，决定是否应用全局默认
    agent_override = subagents_config.agents.get(name)

    overrides = {}

    if agent_override is not None and agent_override.timeout_seconds is not None:
        if agent_override.timeout_seconds != config.timeout_seconds:
            logger.debug("Subagent '%s': timeout overridden (%ss -> %ss)", name, config.timeout_seconds, agent_override.timeout_seconds)
            overrides["timeout_seconds"] = agent_override.timeout_seconds
    elif is_builtin and subagents_config.timeout_seconds != config.timeout_seconds:  # yyds: 全局默认只影响内置
        logger.debug("Subagent '%s': timeout from global default (%ss -> %ss)", name, config.timeout_seconds, subagents_config.timeout_seconds)
        overrides["timeout_seconds"] = subagents_config.timeout_seconds

    if agent_override is not None and agent_override.max_turns is not None:
        if agent_override.max_turns != config.max_turns:
            logger.debug("Subagent '%s': max_turns overridden (%s -> %s)", name, config.max_turns, agent_override.max_turns)
            overrides["max_turns"] = agent_override.max_turns
    elif is_builtin and subagents_config.max_turns is not None and subagents_config.max_turns != config.max_turns:  # yyds: 同理
        logger.debug("Subagent '%s': max_turns from global default (%s -> %s)", name, config.max_turns, subagents_config.max_turns)
        overrides["max_turns"] = subagents_config.max_turns

    effective_model = subagents_config.get_model_for(name)
    if effective_model is not None and effective_model != config.model:
        logger.debug("Subagent '%s': model overridden (%s -> %s)", name, config.model, effective_model)
        overrides["model"] = effective_model

    effective_skills = subagents_config.get_skills_for(name)
    if effective_skills is not None and effective_skills != config.skills:
        logger.debug("Subagent '%s': skills overridden (%s -> %s)", name, config.skills, effective_skills)
        overrides["skills"] = effective_skills

    if overrides:
        config = replace(config, **overrides)  # yyds: dataclass.replace 创建新对象，不修改原 config

    return config


def list_subagents(*, app_config: Any | None = None) -> list[SubagentConfig]:
    """yyds: 列出所有 sub-agent 配置（内置 + 自定义，已应用覆盖）。"""
    configs = []
    for name in get_subagent_names(app_config=app_config):
        config = get_subagent_config(name, app_config=app_config)
        if config is not None:
            configs.append(config)
    return configs


def get_subagent_names(*, app_config: Any | None = None) -> list[str]:
    """yyds: 获取所有 sub-agent 名称 — 内置名称 + custom_agents 名称（去重）。"""
    names = list(BUILTIN_SUBAGENTS.keys())

    subagents_config = _resolve_subagents_app_config(app_config)
    for custom_name in subagents_config.custom_agents:
        if custom_name not in names:  # yyds: 内置和自定义重名时，内置优先
            names.append(custom_name)

    return names


def get_available_subagent_names(*, app_config: Any | None = None) -> list[str]:
    """yyds: 获取运行时可见的 sub-agent 名称 — 过滤掉不可用的。

    过滤规则：
      sandbox 不允许 host bash → 从列表中移除 "bash" sub-agent
      （bash sub-agent 的唯一用途就是跑命令，没 bash 权限就没意义）

    异常兜底：
      检测 bash 权限失败 → 不过滤，返回全部（宁可多暴露一个，不要隐藏可用的）
    """
    names = get_subagent_names(app_config=app_config)
    try:
        host_bash_allowed = is_host_bash_allowed(app_config) if hasattr(app_config, "sandbox") else is_host_bash_allowed()
    except Exception:
        logger.debug("Could not determine host bash availability; exposing all subagents")
        return names

    if not host_bash_allowed:
        names = [name for name in names if name != "bash"]
    return names
