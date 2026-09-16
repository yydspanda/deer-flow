"""Server-resolved settings frozen for one analysis, not process-wide overrides."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator


class SocAnalysisExecutionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    normalization_review_mode: Literal["off", "shadow", "apply"] = "off"
    tenant_policy_enabled: StrictBool = False
    tenant_policy_advisor_enabled: StrictBool = False
    tenant_policy_signal_providers_enabled: StrictBool = False

    @model_validator(mode="after")
    def validate_policy_children(self):
        if not self.tenant_policy_enabled and (self.tenant_policy_advisor_enabled or self.tenant_policy_signal_providers_enabled):
            raise ValueError("企业策略关闭时，LLM 策略建议和策略信号源也必须关闭")
        return self
