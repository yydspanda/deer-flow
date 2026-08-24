"""Versioned tenant knowledge projected into bounded analysis context."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TenantKnowledgeFactKind(StrEnum):
    """Stable classes for reviewed tenant knowledge; none grants action authority."""

    NETWORK_SCOPE = "network_scope"
    DOMAIN_SCOPE = "domain_scope"
    INFRASTRUCTURE_ROLE = "infrastructure_role"
    APPLICATION_IDENTITY = "application_identity"
    PLATFORM_CONTEXT = "platform_context"
    IDENTITY_CONVENTION = "identity_convention"
    DIRECTION_PLAYBOOK = "direction_playbook"
    REVIEWED_EXAMPLE = "reviewed_example"


class TenantProcessObservationPattern(BaseModel):
    """Multi-signal process pattern that must match one canonical observation."""

    model_config = ConfigDict(extra="forbid")

    required_process_names: list[str] = Field(min_length=1, max_length=16)
    required_process_name_prefixes: list[str] = Field(
        default_factory=list,
        max_length=16,
    )
    required_command_terms: list[str] = Field(default_factory=list, max_length=16)
    required_exact_command_lines: list[str] = Field(
        default_factory=list,
        max_length=16,
    )
    required_path_prefixes: list[str] = Field(default_factory=list, max_length=16)

    @field_validator(
        "required_process_names",
        "required_process_name_prefixes",
        "required_command_terms",
        "required_exact_command_lines",
        "required_path_prefixes",
    )
    @classmethod
    def normalize_pattern_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("tenant process observation pattern values must be unique")
        return normalized

    @model_validator(mode="after")
    def require_multiple_signals(self) -> TenantProcessObservationPattern:
        process_identity_count = len(self.required_process_names) + len(self.required_process_name_prefixes)
        if process_identity_count < 2 and not self.required_command_terms and not self.required_exact_command_lines and not self.required_path_prefixes:
            raise ValueError("tenant process observation pattern requires multiple process identities or a command/path constraint")
        return self


class TenantFileObservationPattern(BaseModel):
    """Multi-signal file pattern that must match one canonical file observation."""

    model_config = ConfigDict(extra="forbid")

    required_relations: list[Literal["endpoint_action_target", "observed_artifact"]] = Field(default_factory=list, max_length=4)
    required_file_names: list[str] = Field(default_factory=list, max_length=16)
    required_path_prefixes: list[str] = Field(default_factory=list, max_length=16)
    required_path_suffixes: list[str] = Field(default_factory=list, max_length=16)

    @field_validator(
        "required_file_names",
        "required_path_prefixes",
        "required_path_suffixes",
    )
    @classmethod
    def normalize_pattern_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("tenant file observation pattern values must be unique")
        return normalized

    @model_validator(mode="after")
    def require_multiple_signals(self) -> TenantFileObservationPattern:
        populated_groups = sum(
            bool(values)
            for values in (
                self.required_relations,
                self.required_file_names,
                self.required_path_prefixes,
                self.required_path_suffixes,
            )
        )
        if populated_groups < 2:
            raise ValueError("tenant file observation pattern requires at least two typed constraints")
        return self


class TenantKnowledgeSelector(BaseModel):
    """Relevance selector. Non-empty selector groups are combined with AND."""

    model_config = ConfigDict(extra="forbid")

    exact_ips: list[str] = Field(default_factory=list, max_length=500)
    cidrs: list[str] = Field(default_factory=list, max_length=500)
    domain_suffixes: list[str] = Field(default_factory=list, max_length=100)
    text_terms: list[str] = Field(default_factory=list, max_length=100)
    source_types: list[str] = Field(default_factory=list, max_length=30)
    host_prefixes: list[str] = Field(default_factory=list, max_length=100)
    process_names: list[str] = Field(default_factory=list, max_length=100)
    parent_process_names: list[str] = Field(default_factory=list, max_length=100)
    path_prefixes: list[str] = Field(default_factory=list, max_length=100)
    command_terms: list[str] = Field(default_factory=list, max_length=100)
    parent_command_terms: list[str] = Field(default_factory=list, max_length=100)
    process_observation_patterns: list[TenantProcessObservationPattern] = Field(
        default_factory=list,
        max_length=20,
    )
    file_observation_patterns: list[TenantFileObservationPattern] = Field(
        default_factory=list,
        max_length=20,
    )
    account_patterns: list[str] = Field(default_factory=list, max_length=100)
    uri_prefixes: list[str] = Field(default_factory=list, max_length=100)

    @field_validator(
        "exact_ips",
        "cidrs",
        "domain_suffixes",
        "text_terms",
        "source_types",
        "host_prefixes",
        "process_names",
        "parent_process_names",
        "path_prefixes",
        "command_terms",
        "parent_command_terms",
        "account_patterns",
        "uri_prefixes",
    )
    @classmethod
    def normalize_selector_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("tenant knowledge selector values must be unique")
        return normalized

    @field_validator("account_patterns")
    @classmethod
    def validate_account_patterns(cls, values: list[str]) -> list[str]:
        for pattern in values:
            if len(pattern) > 256:
                raise ValueError("tenant knowledge account pattern exceeds 256 characters")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid tenant knowledge account pattern: {pattern!r}") from exc
        return values

    @model_validator(mode="after")
    def require_selector(self) -> TenantKnowledgeSelector:
        if not any(
            (
                self.exact_ips,
                self.cidrs,
                self.domain_suffixes,
                self.text_terms,
                self.source_types,
                self.host_prefixes,
                self.process_names,
                self.parent_process_names,
                self.path_prefixes,
                self.command_terms,
                self.parent_command_terms,
                self.process_observation_patterns,
                self.file_observation_patterns,
                self.account_patterns,
                self.uri_prefixes,
            )
        ):
            raise ValueError("tenant knowledge selector requires at least one match group")
        return self


class TenantKnowledgeFact(BaseModel):
    """One reviewed fact or playbook entry from a tenant-owned knowledge pack."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,127}$")
    kind: TenantKnowledgeFactKind
    label: str = Field(min_length=1, max_length=256)
    statement: str = Field(min_length=1, max_length=3000)
    selector: TenantKnowledgeSelector
    source_ref: str = Field(min_length=1, max_length=512)
    priority: int = Field(default=100, ge=0, le=1000)
    network_scope_membership: (
        Literal[
            "organization_controlled",
            "organization_external",
            "shared_or_ambiguous",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_network_scope_membership(self) -> TenantKnowledgeFact:
        if (self.kind is TenantKnowledgeFactKind.NETWORK_SCOPE) != (self.network_scope_membership is not None):
            raise ValueError("network_scope facts require network_scope_membership and other fact kinds forbid it")
        return self


class TenantKnowledgeProfile(BaseModel):
    """Code-reviewed static tenant profile used as a bootstrap governed context source."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.tenant_knowledge_profile.v1"] = "soc.tenant_knowledge_profile.v1"
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,127}$")
    version: str = Field(min_length=1, max_length=100)
    integration_names: list[str] = Field(default_factory=list, max_length=30)
    tenant_ids: list[str] = Field(default_factory=list, max_length=100)
    review_status: Literal["reviewed"] = "reviewed"
    review_ref: str = Field(min_length=1, max_length=512)
    max_projected_items: int = Field(default=12, ge=1, le=50)
    max_projected_chars: int = Field(default=12000, ge=500, le=50000)
    facts: list[TenantKnowledgeFact] = Field(min_length=1, max_length=2000)

    @field_validator("integration_names", "tenant_ids")
    @classmethod
    def normalize_profile_scopes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("tenant knowledge profile scopes must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_profile(self) -> TenantKnowledgeProfile:
        if not self.integration_names and not self.tenant_ids:
            raise ValueError("tenant knowledge profile requires an integration or tenant scope")
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("tenant knowledge fact IDs must be unique")
        return self


__all__ = [
    "TenantFileObservationPattern",
    "TenantKnowledgeFact",
    "TenantKnowledgeFactKind",
    "TenantKnowledgeProfile",
    "TenantKnowledgeSelector",
    "TenantProcessObservationPattern",
]
