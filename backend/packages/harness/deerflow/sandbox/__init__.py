# yyds: sandbox/ — 沙箱抽象层
#      核心：Sandbox（抽象基类）+ SandboxProvider（工厂模式）
#      两种实现：
#        - local/   → 本地沙箱（dev 模式，直接在宿主机执行）
#        - community/aio_sandbox/ → Docker 容器沙箱（生产模式，隔离执行）
#      生命周期：SandboxMiddleware 管理 acquire(before_agent) → release(after_agent)
from .sandbox import Sandbox
from .sandbox_provider import SandboxProvider, get_sandbox_provider

__all__ = [
    "Sandbox",
    "SandboxProvider",
    "get_sandbox_provider",
]
