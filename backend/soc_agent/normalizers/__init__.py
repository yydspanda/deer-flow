"""Alert source normalizers.

Normalizers are the only layer that accepts loose vendor/source payloads.
Runtime and downstream components receive canonical contract models only.
"""

from soc_agent.normalizers.alert import normalize_alert_payload

__all__ = ["normalize_alert_payload"]
