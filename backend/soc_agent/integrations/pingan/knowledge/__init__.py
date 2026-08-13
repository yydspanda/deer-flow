"""Reviewed PingAn knowledge packs used by bounded Runtime projection."""

from pathlib import Path

from soc_agent.contracts import TenantKnowledgeProfile
from soc_agent.knowledge import load_tenant_knowledge_profile


def load_pingan_network_direction_profile() -> TenantKnowledgeProfile:
    return load_tenant_knowledge_profile(Path(__file__).with_name("network-direction-v1.json"))


def load_pingan_platform_context_profile() -> TenantKnowledgeProfile:
    return load_tenant_knowledge_profile(Path(__file__).with_name("platform-context-v1.json"))


def load_pingan_internal_systems_profile() -> TenantKnowledgeProfile:
    return load_tenant_knowledge_profile(Path(__file__).with_name("internal-systems-v1.json"))


def load_pingan_tenant_knowledge_profiles() -> tuple[TenantKnowledgeProfile, ...]:
    return (
        load_pingan_network_direction_profile(),
        load_pingan_platform_context_profile(),
        load_pingan_internal_systems_profile(),
    )


__all__ = [
    "load_pingan_internal_systems_profile",
    "load_pingan_network_direction_profile",
    "load_pingan_platform_context_profile",
    "load_pingan_tenant_knowledge_profiles",
]
