"""Config-declared agent middleware loading."""

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware

from deerflow.config.extensions_config import ConfiguredMiddlewareSpec
from deerflow.reflection import resolve_class

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)


def _middleware_constructor_args(entry: str | ConfiguredMiddlewareSpec | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return ``(class_path, kwargs)`` for one config entry."""
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, ConfiguredMiddlewareSpec):
        return entry.class_path, dict(entry.kwargs)
    parsed = ConfiguredMiddlewareSpec.model_validate(entry)
    return parsed.class_path, dict(parsed.kwargs)


def load_configured_extension_middlewares(
    app_config: "AppConfig",
    *,
    agent_middleware_paths: Iterable[str] | None = None,
) -> list[AgentMiddleware]:
    """Instantiate trusted agent-specific and global middleware declarations.

    Entries accept class-path strings or global ``{class, kwargs}`` specs.
    Agent-specific paths load first. Exact class/kwargs duplicates instantiate
    once so a global fallback cannot double-apply an agent-specific guard;
    differently parameterized global instances remain distinct.
    Import, class and constructor errors fail agent creation.
    """
    middlewares: list[AgentMiddleware] = []
    seen: list[tuple[str, dict[str, Any]]] = []
    for entry in [*(agent_middleware_paths or []), *(app_config.extensions.middlewares or [])]:
        class_path, kwargs = _middleware_constructor_args(entry)
        identity = (class_path, kwargs)
        if identity in seen:
            continue
        seen.append(identity)
        middleware_cls = resolve_class(class_path, AgentMiddleware)
        try:
            middleware = middleware_cls(**kwargs)
        except Exception:
            logger.exception("Failed to instantiate configured extension middleware %s", class_path)
            raise
        middlewares.append(middleware)
    return middlewares
