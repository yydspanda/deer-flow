"""Application composition for tenant-aware SOC memory profiles."""

from __future__ import annotations

from soc_agent.integrations.pingan.memory import PingAnSocMemoryProfile
from soc_agent.memory.profiles import SocMemoryProfileRegistry


def build_soc_memory_profile_registry() -> SocMemoryProfileRegistry:
    """Register reviewed tenant profiles ahead of the generic fallback."""

    return SocMemoryProfileRegistry([PingAnSocMemoryProfile()])


__all__ = ["build_soc_memory_profile_registry"]
