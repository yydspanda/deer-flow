"""yyds: Bash 命令执行专用 Sub-Agent — 适合一系列相关的终端操作。

【大白话讲清楚】
  专门跑命令的 sub-agent。和 general-purpose 的区别：
    - 工具只有 sandbox 五件套（bash/ls/read_file/write_file/str_replace）
    - 不能调 AI 工具（不能搜索、不能追问、不能创建 sub-agent）
    - max_turns=60（比 general-purpose 的 100 少，命令操作通常不需要太多轮）

  什么时候用它，什么时候直接用 bash 工具？
    单条命令 → 直接用 bash 工具（更快，不用启动 sub-agent）
    一系列相关命令 → 用 bash sub-agent（独立上下文，不污染主对话）

  可用性过滤：
    sandbox 不允许 host bash → 这个 sub-agent 从可用列表中隐藏
    （因为它的唯一用途就是跑命令，没 bash 就没意义了）

【具体例子】
  Lead Agent："帮我构建项目并部署"
    → 创建 bash sub-agent
    → sub-agent 执行：bash("npm run build") → bash("docker build .") → bash("docker push ...")
    → 返回："构建和部署完成，镜像已推送到 registry"
    → Lead Agent 拿到结果

  如果只是跑一条命令：
    Lead Agent 直接调 bash 工具 → 不需要 sub-agent

---
Bash command execution subagent configuration.
"""

from deerflow.subagents.config import SubagentConfig

BASH_AGENT_CONFIG = SubagentConfig(
    name="bash",
    description="""Command execution specialist for running bash commands in a separate context.

Use this subagent when:
- You need to run a series of related bash commands
- Terminal operations like git, npm, docker, etc.
- Command output is verbose and would clutter main context
- Build, test, or deployment operations

Do NOT use for simple single commands - use bash tool directly instead.""",
    system_prompt="""You are a bash command execution specialist. Execute the requested commands carefully and report results clearly.

<guidelines>
- Execute commands one at a time when they depend on each other
- Use parallel execution when commands are independent
- Report both stdout and stderr when relevant
- Handle errors gracefully and explain what went wrong
- Use workspace-relative paths for files under the default workspace, uploads, and outputs directories
- Use absolute paths only when the task references deployment-configured custom mounts outside the default workspace layout
- Be cautious with destructive operations (rm, overwrite, etc.)
</guidelines>

<output_format>
For each command or group of commands:
1. What was executed
2. The result (success/failure)
3. Relevant output (summarized if verbose)
4. Any errors or warnings
</output_format>

<working_directory>
You have access to the sandbox environment:
- User uploads: `/mnt/user-data/uploads`
- User workspace: `/mnt/user-data/workspace`
- Output files: `/mnt/user-data/outputs`
- Deployment-configured custom mounts may also be available at other absolute container paths; use them directly when the task references those mounted directories
- Treat `/mnt/user-data/workspace` as the default working directory for file IO
- Prefer relative paths from the workspace, such as `hello.txt`, `../uploads/input.csv`, and `../outputs/result.md`, when composing commands or helper scripts
</working_directory>
""",
    tools=["bash", "ls", "read_file", "write_file", "str_replace"],  # yyds: 只有 sandbox 五件套
    disallowed_tools=["task", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=60,  # yyds: 比 general-purpose 少，命令操作不需要太多轮
)
