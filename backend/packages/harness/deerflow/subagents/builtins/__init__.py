"""yyds: 内置 Sub-Agent 注册表 — 两个出厂自带的 sub-agent。

【大白话讲清楚】
  BUILTIN_SUBAGENTS 是一个 dict，key 是 sub-agent 名，value 是 SubagentConfig。
  registry.py 优先查这个 dict，找不到再去 config.yaml 的 custom_agents 里找。

  两个内置 agent 的定位：
    general-purpose：全能型，什么都能做，适合复杂多步骤任务
    bash：专业型，只会跑命令，适合一系列相关的终端操作

【具体例子】
  task_tool 收到 subagent_type="general-purpose"
    → registry 查 BUILTIN_SUBAGENTS["general-purpose"] → 找到 SubagentConfig
    → 用这个 config 创建 SubagentExecutor
    → sub-agent 开始执行

  task_tool 收到 subagent_type="my-custom-agent"
    → registry 查 BUILTIN_SUBAGENTS → 找不到
    → 去 config.yaml custom_agents 里找 → 找到了 → 用那个 config

---
Built-in subagent configurations.
"""

from .bash_agent import BASH_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG

__all__ = [
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
]

BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
}
