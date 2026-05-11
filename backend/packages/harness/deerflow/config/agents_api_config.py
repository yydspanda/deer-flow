# yyds: 自定义 Agent 管理 API 配置，控制 SOUL.md/USER.md 等路由的读写权限。
# yyds: 默认关闭(enabled=False)，开启后网关才接受自定义 Agent 的配置读写请求。
"""Configuration for the custom agents management API."""

from pydantic import BaseModel, Field


class AgentsApiConfig(BaseModel):
    """Configuration for custom-agent and user-profile management routes."""

    enabled: bool = Field(
        default=False,
        description=("Whether to expose the custom-agent management API over HTTP. When disabled, the gateway rejects read/write access to custom agent SOUL.md, config, and USER.md prompt-management routes."),
    )


_agents_api_config: AgentsApiConfig = AgentsApiConfig()


def get_agents_api_config() -> AgentsApiConfig:
    """Get the current agents API configuration."""
    return _agents_api_config


def set_agents_api_config(config: AgentsApiConfig) -> None:
    """Set the agents API configuration."""
    global _agents_api_config
    _agents_api_config = config


def load_agents_api_config_from_dict(config_dict: dict) -> None:
    """Load agents API configuration from a dictionary."""
    global _agents_api_config
    _agents_api_config = AgentsApiConfig(**config_dict)
