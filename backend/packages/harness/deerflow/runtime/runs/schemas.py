# yyds: 运行状态和断连模式的枚举定义
"""Run status and disconnect mode enums."""

from enum import StrEnum


# yyds: 运行生命周期状态枚举：pending→running→success/error/timeout/interrupted
class RunStatus(StrEnum):
    """Lifecycle status of a single run."""

    pending = "pending"
    running = "running"
    success = "success"
    error = "error"
    timeout = "timeout"
    interrupted = "interrupted"


# yyds: SSE消费者断连行为：cancel取消运行，continue继续执行
class DisconnectMode(StrEnum):
    """Behaviour when the SSE consumer disconnects."""

    cancel = "cancel"
    continue_ = "continue"
