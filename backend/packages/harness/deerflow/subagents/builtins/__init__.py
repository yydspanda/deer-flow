# yyds: 内置 sub-agent 注册表 —— 两个内置 agent：general-purpose（全能型）和 bash（命令执行型）
# yyds: BUILTIN_SUBAGENTS 是 dict，key=sub-agent 名称，value=SubagentConfig
"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG

__all__ = [
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
}
