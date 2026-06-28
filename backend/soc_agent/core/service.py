"""Stable public service entry points for SOC Agent use cases."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import AnalysisRun
from soc_agent.core.runtime import analyze_alert


class SocAnalysisService:
    """Application service used by CLI, API, daemon, and future Web UI.

    Transport layers should call this service instead of directly assembling
    pipeline steps or touching repositories/adapters.
    """

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun:
        return analyze_alert(payload)
