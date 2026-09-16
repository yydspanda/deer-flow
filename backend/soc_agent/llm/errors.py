"""Sanitized provider protocol errors, separate from model output validation."""


class SocLLMResponseProtocolError(RuntimeError):
    """The provider returned text where the SDK expected a completion envelope."""

    public_message = "模型服务返回格式异常，未能读取完整研判答复。请检查模型服务的流式配置后重试。"

    def __init__(self) -> None:
        super().__init__(self.public_message)
