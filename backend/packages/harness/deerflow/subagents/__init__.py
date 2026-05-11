# yyds: Sub-Agent 子系统 —— Lead Agent 将复杂任务委托给独立运行的子 Agent
# yyds: 核心数据流：task_tool → SubagentExecutor.execute_async() → 后台线程池 → isolated event loop → astream → 结果回传
# yyds: 关键设计：递归防护（disallowed_tools=["task"]）、并发限制（MAX_CONCURRENT_SUBAGENTS=3）、超时控制（默认 900s）
from .config import SubagentConfig
from .executor import SubagentExecutor, SubagentResult
from .registry import get_available_subagent_names, get_subagent_config, list_subagents

__all__ = [
    "SubagentConfig",
    "SubagentExecutor",
    "SubagentResult",
    "get_available_subagent_names",
    "get_subagent_config",
    "list_subagents",
]
