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
