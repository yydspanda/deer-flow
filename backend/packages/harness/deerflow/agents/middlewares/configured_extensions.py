"""Config-declared agent middleware loading."""

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from langchain.agents.middleware import AgentMiddleware

from deerflow.reflection import resolve_class

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)


def load_configured_extension_middlewares(
    app_config: "AppConfig",
    *,
    agent_middleware_paths: Iterable[str] | None = None,
) -> list[AgentMiddleware]:
    """Instantiate config-declared agent middlewares.

    Each entry is a zero-argument ``AgentMiddleware`` class path in
    ``module.path:ClassName`` format. Import, attribute, and subclass validation
    intentionally go through the shared reflection resolver so failures carry
    the same actionable dependency hints as models, tools, sandbox providers,
    and guardrail providers. Per-agent paths are operator-owned and load before
    process-global extension paths. Exact duplicate paths are instantiated only
    once so a global fallback cannot double-apply an agent-specific guard.
    """
    middlewares: list[AgentMiddleware] = []
    middleware_paths = _deduplicate_paths(
        [
            *(agent_middleware_paths or []),
            *(app_config.extensions.middlewares or []),
        ]
    )
    for middleware_path in middleware_paths:
        middleware_cls = resolve_class(middleware_path, AgentMiddleware)
        try:
            middleware = middleware_cls()
        except Exception:
            logger.exception("Failed to instantiate configured extension middleware %s", middleware_path)
            raise
        middlewares.append(middleware)
    return middlewares


def _deduplicate_paths(paths: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique
