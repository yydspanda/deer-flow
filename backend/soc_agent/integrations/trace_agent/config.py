"""Explicit configuration for the optional SOC-to-Trace HTTP boundary."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class TraceClientSettings:
    enabled: bool = False
    base_url: str | None = field(default=None, repr=False)
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    max_response_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("Trace enabled must be a boolean")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Trace timeout must be a finite positive number")
        if type(self.max_response_bytes) is not int or self.max_response_bytes <= 0:
            raise ValueError("Trace response limit must be a positive integer")
        if self.base_url is not None:
            try:
                if not isinstance(self.base_url, str) or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.base_url):
                    raise ValueError("invalid URL character")
                parsed = urlsplit(self.base_url)
                valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.port != 0 and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise ValueError("Trace base URL must be HTTP(S) without credentials, query or fragment")
        if self.api_key is not None and (not isinstance(self.api_key, str) or not self.api_key.strip() or not self.api_key.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in self.api_key)):
            raise ValueError("Trace API key must be a nonempty valid header value")
        if self.enabled and (not self.base_url or not self.api_key):
            raise ValueError("Enabled Trace client requires an explicit base URL and API key")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> TraceClientSettings:
        """Read only the supplied mapping; importing this module never reads env."""

        raw_enabled = env.get("SOC_TRACE_AGENT_ENABLED", "false").strip().lower()
        if raw_enabled not in {"true", "false", "1", "0"}:
            raise ValueError("SOC_TRACE_AGENT_ENABLED must be true, false, 1 or 0")
        try:
            timeout = float(env.get("SOC_TRACE_AGENT_TIMEOUT_SECONDS", "30"))
            limit = int(env.get("SOC_TRACE_AGENT_MAX_RESPONSE_BYTES", str(4 * 1024 * 1024)))
        except (TypeError, ValueError):
            raise ValueError("Trace timeout and response limit must be numeric") from None
        return cls(enabled=raw_enabled in {"true", "1"}, base_url=env.get("SOC_TRACE_AGENT_BASE_URL") or None, api_key=env.get("SOC_TRACE_AGENT_API_KEY") or None, timeout_seconds=timeout, max_response_bytes=limit)
