# yyds: lead_agent 子包入口 — 导出 make_lead_agent，它是 LangGraph 注册的顶层 Agent 工厂
from .agent import make_lead_agent

__all__ = ["make_lead_agent"]
