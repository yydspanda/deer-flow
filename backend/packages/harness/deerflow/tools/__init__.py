# yyds: 工具模块公共接口，导出工具注册/发现功能，skill_manage_tool通过懒加载避免循环依赖
from .tools import get_available_tools

__all__ = ["get_available_tools", "skill_manage_tool"]


def __getattr__(name: str):
    if name == "skill_manage_tool":
        from .skill_manage_tool import skill_manage_tool

        return skill_manage_tool
    raise AttributeError(name)
