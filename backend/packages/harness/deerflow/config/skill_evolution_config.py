# yyds: Agent 自主技能演化配置，允许 Agent 在 skills/custom 下创建和修改技能文件。
# yyds: moderation_model_name 可指定独立的安全审核模型，默认使用主对话模型。
from pydantic import BaseModel, Field


class SkillEvolutionConfig(BaseModel):
    """Configuration for agent-managed skill evolution."""

    enabled: bool = Field(
        default=False,
        description="Whether the agent can create and modify skills under skills/custom.",
    )
    moderation_model_name: str | None = Field(
        default=None,
        description="Optional model name for skill security moderation. Defaults to the primary chat model.",
    )
