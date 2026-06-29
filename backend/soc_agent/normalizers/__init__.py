"""Alert source normalizers.

Normalizers are the only layer that accepts loose vendor/source payloads.
Runtime and downstream components receive canonical contract models only.
"""

from soc_agent.normalizers.alert import normalize_alert_payload
from soc_agent.normalizers.mapping import load_mapping_config, normalize_with_mapping

__all__ = ["load_mapping_config", "normalize_alert_payload", "normalize_with_mapping"]
