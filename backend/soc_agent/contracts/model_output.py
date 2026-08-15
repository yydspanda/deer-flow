"""Small provider-owned output contracts for bounded SOC model calls."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import Verdict

ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION = "soc.analysis_model_output.v4"


class AnalysisModelCoreOutputV2(BaseModel):
    """Minimum model-owned result needed to preserve a supported verdict.

    Optional reasoning, scenario, direction, role, and guidance sections are
    validated independently. Runtime materializes their stable internal
    representation and never asks the provider to copy evidence values.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["soc.analysis_model_output.v2"]
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=4000)
    decision_evidence_refs: list[str] = Field(min_length=1, max_length=20)
    decision_context_refs: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=8000)
    recommended_action: str = Field(min_length=1, max_length=1000)

    @field_validator("decision_evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("decision_evidence_refs must be unique")
        if any(not re.fullmatch(r"E-[A-F0-9]{12}", value) for value in values):
            raise ValueError("decision_evidence_refs must use E-* references")
        return values

    @field_validator("decision_context_refs")
    @classmethod
    def validate_context_refs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("decision_context_refs must be unique")
        if any(not re.fullmatch(r"(?:S|A|M|C|T)-[A-F0-9]{12}", value) for value in values):
            raise ValueError("decision_context_refs must use S/A/M/C/T references")
        return values


class AnalysisModelCoreOutputV3(BaseModel):
    """Current provider-owned core; Runtime owns the reasoning graph.

    Optional scenario, direction, and role objects carry their own rationale
    and exact catalog references. Runtime assigns stable ``R-*`` identifiers
    after validation, so one malformed model-generated cross-reference cannot
    invalidate otherwise usable optional sections.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["soc.analysis_model_output.v3"]
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=4000)
    decision_evidence_refs: list[str] = Field(min_length=1, max_length=20)
    decision_context_refs: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=8000)
    recommended_action: str = Field(min_length=1, max_length=1000)

    @field_validator("decision_evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("decision_evidence_refs must be unique")
        if any(not re.fullmatch(r"E-[A-F0-9]{12}", value) for value in values):
            raise ValueError("decision_evidence_refs must use E-* references")
        return values

    @field_validator("decision_context_refs")
    @classmethod
    def validate_context_refs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("decision_context_refs must be unique")
        if any(not re.fullmatch(r"(?:S|A|M|C|T)-[A-F0-9]{12}", value) for value in values):
            raise ValueError("decision_context_refs must use S/A/M/C/T references")
        return values


class AnalysisModelCoreOutputV4(AnalysisModelCoreOutputV3):
    """Current core after model-facing aliases are restored by Runtime."""

    schema_version: Literal["soc.analysis_model_output.v4"]


__all__ = [
    "ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION",
    "AnalysisModelCoreOutputV2",
    "AnalysisModelCoreOutputV3",
    "AnalysisModelCoreOutputV4",
]
