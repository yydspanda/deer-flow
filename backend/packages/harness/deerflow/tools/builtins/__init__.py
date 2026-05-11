# yyds: tools/builtins/ — 内置工具集
#      这些工具由 make_lead_agent() 通过 BUILTIN_TOOLS 注册到 Agent
#      不同于 sandbox/tools.py（沙箱操作），这些是业务逻辑层面的工具
#      - present_file_tool  — 将文件展示给用户（artifacts 渲染）
#      - ask_clarification_tool — 向用户提问确认（被 ClarificationMiddleware 拦截）
#      - view_image_tool — 读取图片并 base64 编码（让 LLM 看图）
#      - task_tool — 派任务给 sub-agent（Ultra 模式的核心）
#      - setup_agent — agent 初始化
#      - update_agent — agent 更新
from .clarification_tool import ask_clarification_tool
from .present_file_tool import present_file_tool
from .setup_agent_tool import setup_agent
from .task_tool import task_tool
from .update_agent_tool import update_agent
from .view_image_tool import view_image_tool

__all__ = [
    "setup_agent",
    "update_agent",
    "present_file_tool",
    "ask_clarification_tool",
    "view_image_tool",
    "task_tool",
]
