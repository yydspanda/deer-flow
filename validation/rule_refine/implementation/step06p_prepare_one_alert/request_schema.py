"""Reviewed API request model copied without importing the application."""

from typing import Union
from uuid import uuid4

from pydantic import BaseModel, Field


class WorkFlowTaskInput(BaseModel):
    app_code: str = Field(..., description="子系统编码", example="zeus")
    flow_id: str = Field(..., description="工作流或者agent的id", example="alert_agent")
    session_id: str = Field(
        ...,
        description="会话ID,可选参数，由调用方控制，如果传入，则代表维护同session_id的连续对话关系",
        default_factory=lambda: uuid4().hex,
    )
    alert_id: str = Field(..., description="预警ID")
    alert_data: Union[dict, None] = Field(None, description="用于研判的全量数据")
