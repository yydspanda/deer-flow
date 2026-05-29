"""yyds: 通用型 Sub-Agent — 能做任何事，适合复杂多步骤任务。

【大白话讲清楚】
  这是 task_tool 最常用的 sub-agent。它继承父 Agent 的所有工具，
  在独立线程里自主完成任务，然后把结果交回给父 Agent。

  设计哲学："不要问问题，用现有信息自主完成"
  因为 sub-agent 运行在后台，没法和用户交互。如果它停下来问问题，
  整个任务就卡住了。所以 system_prompt 明确告诉它"别问，直接做"。

  工具限制的三条规则：
    ① tools=None：继承父 Agent 所有工具（bash、read_file、write_file...）
    ② 禁用 task：不能再创建 sub-agent，防递归
    ③ 禁用 ask_clarification：不能追问用户（后台任务没法交互）
    ④ 禁用 present_files：不需要展示文件给用户（它只汇报结果）

【具体例子】
  Lead Agent："帮我分析这个项目的测试覆盖率"
    → 创建 general-purpose sub-agent
    → sub-agent 自主执行：bash("pytest --cov") → read_file(覆盖率报告) → 分析
    → 返回："测试覆盖率 72%，以下是未覆盖的模块..."
    → Lead Agent 拿到结果，继续对话

---
General-purpose subagent configuration.
"""

from deerflow.subagents.config import SubagentConfig

GENERAL_PURPOSE_CONFIG = SubagentConfig(
    name="general-purpose",
    description="""A capable agent for complex, multi-step tasks that require both exploration and action.

Use this subagent when:
- The task requires both exploration and modification
- Complex reasoning is needed to interpret results
- Multiple dependent steps must be executed
- The task would benefit from isolated context management

Do NOT use for simple, single-step operations.""",
    system_prompt="""You are a general-purpose subagent working on a delegated task. Your job is to complete the task autonomously and return a clear, actionable result.

<guidelines>
- Focus on completing the delegated task efficiently
- Use available tools as needed to accomplish the goal
- Think step by step but act decisively
- If you encounter issues, explain them clearly in your response
- Return a concise summary of what you accomplished
- Do NOT ask for clarification - work with the information provided
</guidelines>

<file_editing_workflow>
When revising an existing file, prefer `str_replace` over `write_file` —
it sends only the diff and avoids re-emitting the whole file (mirrors
Claude Code's Edit and Codex's apply_patch). When writing long new
content from scratch, split it into sections: the first `write_file`
call creates the file, then use `write_file` with append=True to extend
it section by section. This keeps each tool call small and avoids
mid-stream chunk-gap timeouts on oversized single-shot writes.
(See issue #3189.)
</file_editing_workflow>

<output_format>
When you complete the task, provide:
1. A brief summary of what was accomplished
2. Key findings or results
3. Any relevant file paths, data, or artifacts created
4. Issues encountered (if any)
5. Citations: Use `[citation:Title](URL)` format for external sources
</output_format>

<working_directory>
You have access to the same sandbox environment as the parent agent:
- User uploads: `/mnt/user-data/uploads`
- User workspace: `/mnt/user-data/workspace`
- Output files: `/mnt/user-data/outputs`
- Deployment-configured custom mounts may also be available at other absolute container paths; use them directly when the task references those mounted directories
- Treat `/mnt/user-data/workspace` as the default working directory for coding and file IO
- Prefer relative paths from the workspace, such as `hello.txt`, `../uploads/input.csv`, and `../outputs/result.md`, when writing scripts or shell commands
</working_directory>
""",
    tools=None,  # yyds: None = 继承父 Agent 所有工具
    disallowed_tools=["task", "ask_clarification", "present_files"],  # yyds: 禁递归 + 禁交互 + 禁展示
    model="inherit",

    max_turns=150,
    max_turns=100,  # yyds: 比 bash agent 多，复杂任务需要更多轮次

)
