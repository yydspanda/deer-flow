"""Reviewed PingAn knowledge packs used by bounded Runtime projection."""

from pathlib import Path

from soc_agent.contracts import TenantKnowledgeProfile
from soc_agent.knowledge import load_tenant_knowledge_profile


def load_pingan_network_direction_profile() -> TenantKnowledgeProfile:
    return load_tenant_knowledge_profile(Path(__file__).with_name("network-direction-v1.json"))


__all__ = ["load_pingan_network_direction_profile"]
