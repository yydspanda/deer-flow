# yyds: reflection/ — 动态加载模块
#      核心函数 resolve_variable("deerflow.sandbox.tools:bash_tool")
#      把字符串翻译成实际的 Python 对象
#      用途：config.yaml 里用字符串配置工具/模型/中间件，运行时通过这个模块加载
from .resolvers import resolve_class, resolve_variable

__all__ = ["resolve_class", "resolve_variable"]
