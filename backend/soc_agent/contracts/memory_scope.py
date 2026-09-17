"""Display-only Memory scope projection, never accepted by governance commands."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryScopeOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    values: list[str]
    covered_values: list[str] = Field(default_factory=list)
    kind: Literal["additional", "covered", "similarity"]


class MemoryScopeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_scope_view.v1"] = "soc.memory_scope_view.v1"
    required_details: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    unresolved_fingerprint_keys: list[str] = Field(default_factory=list)
    options: list[MemoryScopeOption] = Field(default_factory=list)


class MemorySourceScopeGroup(BaseModel):
    facet_key: Literal["entity", "role_entity"]
    value_prefix: str


class MemorySourceScopeOption(MemorySourceScopeGroup):
    value: str
    sample_count: int = Field(ge=1)
    from_current_alert: bool
    source_alert_ids: list[str] = Field(default_factory=list, max_length=5)


class MemorySourceScopeOptions(BaseModel):
    groups: list[MemorySourceScopeGroup]
    items: list[MemorySourceScopeOption] = Field(max_length=50)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    source_sample_count: int = Field(ge=0)
