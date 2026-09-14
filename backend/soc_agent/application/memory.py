"""Application composition for tenant-aware SOC memory profiles."""

from __future__ import annotations

import os

from soc_agent.integrations.pingan.memory import PingAnSocMemoryProfile
from soc_agent.memory.profiles import SocMemoryProfileRegistry


def build_soc_memory_profile_registry() -> SocMemoryProfileRegistry:
    """Register reviewed tenant profiles ahead of the generic fallback."""

    semantic_features = os.environ.get("SOC_NORMALIZATION_ASSIST_MODE", "off").strip().lower() == "apply"
    return SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=semantic_features)])


__all__ = ["build_soc_memory_profile_registry"]
