"""yyds: Agent 创建工具 — bootstrap 模式下让 LLM 帮你"捏"一个自定义 Agent。

【大白话讲清楚】
  用户想要一个专属 Agent（比如"代码审查专家"、"数据分析师"），
  但不会写配置文件。DeerFlow 的方案是：

  用户在 IM 里输入 /bootstrap 告诉我你想要什么 Agent
  → 系统进入 bootstrap 模式（is_bootstrap=True）
  → 创建一个最小化的 bootstrap Agent（只有 setup_agent 一个额外工具）
  → LLM 根据用户描述，生成 SOUL.md（人格）+ config.yaml（配置）
  → 调用 setup_agent 写入磁盘
  → 自定义 Agent 创建完成

  setup_agent 干的事情很简单：
    ① 验证 agent_name（防路径穿越）
    ② 确定目录（用户隔离：users/{user_id}/agents/{agent_name}/）
    ③ 写 SOUL.md（Agent 人格文件）
    ④ 写 config.yaml（name + description + skills）
    ⑤ 失败时自动清理（只删新建的目录，不误删已有的）

【具体例子】
  用户在 Slack 里输入：/bootstrap 我想要一个 Python 代码审查专家

  系统创建 bootstrap Agent → LLM 生成：
    SOUL.md = "你是一个 Python 代码审查专家。你专注于..."
    description = "Python code review specialist"
    skills = ["code-review"]

  setup_agent 执行：
    ① agent_name = "python-reviewer"（从 runtime.context 取）
    ② user_id = resolve_runtime_user_id(runtime) → "user_abc123"
    ③ agent_dir = .deer-flow/users/user_abc123/agents/python-reviewer/
    ④ 写 config.yaml: {name: "python-reviewer", description: "...", skills: [...]}
    ⑤ 写 SOUL.md: "你是..."
    ⑥ 返回 Command(update={"created_agent_name": "python-reviewer", ...})

  下次用户跟这个 Agent 聊天时 → 系统加载 SOUL.md → Agent 有了人格。

  异常流程 A（agent_name 不合法）：
    agent_name = "../../etc/passwd"
    → validate_agent_name() 抛 ValueError
    → except 块捕获 → is_new_dir=False → 不清理（没创建过目录）
    → 返回 ToolMessage: "Error: ..."

  异常流程 B（写 SOUL.md 时磁盘满）：
    agent_dir 刚创建成功，写 SOUL.md 失败
    → except 块：is_new_dir=True → shutil.rmtree(agent_dir) 清理
    → 返回 ToolMessage: "Error: ..."
    → 磁盘上不留垃圾

  异常流程 C（默认 Agent，没有 agent_name）：
    agent_name = None
    → agent_dir = paths.base_dir（全局目录）
    → 只写 SOUL.md，不写 config.yaml
    → 不创建子目录 → is_new_dir 可能为 False → 不清理

【加载条件】
  只在 bootstrap 模式加载（agent.py 第 538-540 行）：
    if is_bootstrap:
        tools = get_available_tools(...) + [setup_agent]

  触发方式：用户在 IM 里输入 /bootstrap 命令
  （channels/manager.py 第 951-956 行）

【在链中的位置】
  调用者：bootstrap Agent（特殊的最小化 Agent）
  注册位置：agent.py 的 is_bootstrap 分支
  下游：paths.py（确定目录）+ user_context.py（确定用户）
  持久化：磁盘文件（.deer-flow/users/{uid}/agents/{name}/）
  配套工具：update_agent（已创建的自定义 Agent 用来更新自己）

【用户隔离】
  多用户系统下，每个用户的 Agent 隔离存储：
    用户 A 的 Agent → users/user_a/agents/my-agent/
    用户 B 的 Agent → users/user_b/agents/my-agent/
  同名不冲突。user_id 从 runtime.context["user_id"] 取（gateway 注入的认证信息），
  没有认证时回退到 DEFAULT_USER_ID。

---
Agent creation tool for the bootstrap flow. Writes SOUL.md and config.yaml
to the user-scoped agent directory, with automatic cleanup on failure.
"""

import logging

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from deerflow.config.agents_config import validate_agent_name
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)


@tool(parse_docstring=True)
def setup_agent(
    soul: str,
    description: str,
    runtime: Runtime,
    skills: list[str] | None = None,
) -> Command:
    """yyds: 创建自定义 Agent — 写 SOUL.md + config.yaml 到用户隔离目录。

    例子：
      用户："我想要一个 Python 代码审查专家"
      LLM 调用：setup_agent(
          soul="你是一个 Python 代码审查专家...",
          description="Python code review specialist",
          skills=["code-review"]
      )
      → 创建 .deer-flow/users/{uid}/agents/{name}/SOUL.md + config.yaml
      → 返回 Command(update={"created_agent_name": ..., "messages": [...]})

    执行步骤：
      ① 验证 agent_name（防路径穿越）
      ② 确定目录（有 agent_name → 用户隔离目录，无 → 全局目录）
      ③ 创建目录（如果不存在）
      ④ 自定义 Agent 写 config.yaml（name + description + skills）
      ⑤ 写 SOUL.md（Agent 人格文件）
      ⑥ 返回 Command 更新 state
      ⑦ 失败时自动清理（只删本次新建的目录）

    参数：
      soul: SOUL.md 的完整内容（Agent 人格、专业领域、回复风格）
      description: 一行描述（在 Agent 列表里显示）
      skills: 技能白名单。None=用所有技能，[]=不用技能
    ---
    Setup the custom DeerFlow agent.

    Args:
        soul: Full SOUL.md content defining the agent's personality and behavior.
        description: One-line description of what the agent does.
        skills: Optional list of skill names this agent should use. None means use all enabled skills, empty list means no skills.
    """

    agent_name: str | None = runtime.context.get("agent_name") if runtime.context else None
    agent_dir = None
    is_new_dir = False

    try:
        # ① 验证 agent_name — 防 "../../etc/passwd" 这种路径穿越攻击
        agent_name = validate_agent_name(agent_name)
        paths = get_paths()

        if agent_name:
            # ② 自定义 Agent → 写到用户隔离目录（users/{uid}/agents/{name}/）
            user_id = resolve_runtime_user_id(runtime)
            agent_dir = paths.user_agent_dir(user_id, agent_name)
        else:
            # ② 默认 Agent → 写到全局目录（.deer-flow/）
            agent_dir = paths.base_dir

        is_new_dir = not agent_dir.exists()
        agent_dir.mkdir(parents=True, exist_ok=True)

        if agent_name:
            # ④ 自定义 Agent 写 config.yaml（默认 Agent 不写，它没有配置文件）
            config_data: dict = {"name": agent_name}
            if description:
                config_data["description"] = description
            if skills is not None:
                config_data["skills"] = skills

            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        # ⑤ 写 SOUL.md — Agent 人格的核心文件
        soul_file = agent_dir / "SOUL.md"
        soul_file.write_text(soul, encoding="utf-8")

        logger.info(f"[agent_creator] Created agent '{agent_name}' at {agent_dir}")
        # ⑥ 返回 Command — 同时更新 created_agent_name 和 messages
        return Command(
            update={
                "created_agent_name": agent_name,
                "messages": [ToolMessage(content=f"Agent '{agent_name}' created successfully!", tool_call_id=runtime.tool_call_id)],
            }
        )

    except Exception as e:
        import shutil

        # ⑦ 失败清理 — 只删本次新建的目录（is_new_dir=True）
        # 如果目录之前就存在（is_new_dir=False），不删（里面有别人的数据）
        if agent_name and is_new_dir and agent_dir is not None and agent_dir.exists():
            shutil.rmtree(agent_dir)
        logger.error(f"[agent_creator] Failed to create agent '{agent_name}': {e}", exc_info=True)
        return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id)]})
