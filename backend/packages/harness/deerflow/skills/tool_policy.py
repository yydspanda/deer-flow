"""yyds: 工具策略 — 技能怎么限制 Agent 能用什么工具？

问题：技能可能有危险，不该让加载了"数据分析"技能的 Agent 也能执行 shell 命令。
方案：每个技能在 SKILL.md 里声明 allowed-tools（允许使用的工具列表），
      多个技能的声明取并集，没声明的技能不参与限制。

三种状态：
  None → 没有任何技能声明 allowed-tools → 不限制，保留全部工具
  set() → 有技能声明了但列表为空 → 不给用任何工具
  set({"search", "read_file"}) → 只给用这几个工具
"""

import logging
from typing import Protocol

from deerflow.skills.types import Skill

logger = logging.getLogger(__name__)


class NamedTool(Protocol):
    """只要有 name 属性就算"工具"——Protocol 鸭子类型，不强制继承。"""

    name: str


def allowed_tool_names_for_skills(skills: list[Skill]) -> set[str] | None:
    """汇总所有技能的 allowed-tools 声明。

    关键逻辑：
      如果所有技能都没声明 allowed-tools → 返回 None（不限制）
      只要有一个技能声明了 → 返回并集 set
      声明了但列表为空 → 该技能不贡献任何工具名，但触发了限制模式

    为什么是"并集"而不是"交集"？
      因为 Agent 同时加载多个技能，任何一个技能需要的工具都应该可用。
      比如技能 A 要 search，技能 B 要 read_file，那两个都该给。
    """
    if not skills:
        return None

    allowed: set[str] = set()
    has_explicit_declaration = False
    for skill in skills:
        if skill.allowed_tools is None:
            continue
        has_explicit_declaration = True
        if not skill.allowed_tools:
            logger.info("Skill %s declared empty allowed-tools", skill.name)
        allowed.update(skill.allowed_tools)

    if not has_explicit_declaration:
        return None
    return allowed


def filter_tools_by_skill_allowed_tools[ToolT: NamedTool](tools: list[ToolT], skills: list[Skill]) -> list[ToolT]:
    """根据技能声明过滤工具列表。

    泛型 ToolT: NamedTool — 只要工具有 name 属性就能被过滤。
    返回值类型跟传入的工具类型一致（保持类型信息不丢失）。
    """
    allowed = allowed_tool_names_for_skills(skills)
    if allowed is None:
        return tools

    return [tool for tool in tools if tool.name in allowed]
