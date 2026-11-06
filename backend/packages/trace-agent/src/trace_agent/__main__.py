"""Configuration-only checks and explicitly requested standalone serving."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import re
import sys
from collections.abc import Mapping

FACTORY_PATTERN = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", flags=re.ASCII)


def _settings(environment: Mapping[str, str], *, host: str, port: int | None):
    try:
        selected_port = port if port is not None else int(environment.get("TRACE_AGENT_PORT", "8300"))
    except ValueError:
        raise ValueError("TRACE_AGENT_PORT must be an integer") from None
    if not 1 <= selected_port <= 65535:
        raise ValueError("TRACE_AGENT_PORT must be between 1 and 65535")
    factory = environment.get("TRACE_AGENT_ENGINE_FACTORY", "")
    if factory and not FACTORY_PATTERN.fullmatch(factory):
        raise ValueError("TRACE_AGENT_ENGINE_FACTORY must be module:function")
    key = environment.get("TRACE_AGENT_API_KEY", "")
    return key, factory, host, selected_port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trace service development boundary; no real investigation engine is included.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check", help="Validate configuration only; does not probe or initialize workers")
    serve = subcommands.add_parser("serve", help="Serve the independently configured Trace HTTP boundary")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    try:
        key, factory, host, port = _settings(os.environ, host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", None))
    except ValueError as exc:
        if args.command == "check":
            print(json.dumps({"status": "not_ready", "detail": str(exc), "worker_readiness": "not_checked"}))
        else:
            print(str(exc), file=sys.stderr)
        return 2
    if args.command == "check":
        configured = bool(key.strip() and factory)
        print(json.dumps({"status": "configured" if configured else "not_ready", "api_key_configured": bool(key.strip()), "engine_factory_configured": bool(factory), "worker_readiness": "not_checked", "host": host, "port": port}))
        return 0 if configured else 2
    if not key.strip():
        print("TRACE_AGENT_API_KEY is required to serve the Trace API.", file=sys.stderr)
        return 2
    try:
        from .engine import UnavailableEngine
        from .service import create_app

        engine = UnavailableEngine()
        if factory:
            module_name, attribute = factory.split(":", 1)
            builder = getattr(importlib.import_module(module_name), attribute)
            if not callable(builder) or inspect.iscoroutinefunction(builder):
                raise TypeError("Engine factory must be a synchronous callable")
            engine = builder()
            if not all(inspect.iscoroutinefunction(getattr(engine, name, None)) for name in ("create", "get", "resume", "cancel", "health")):
                raise TypeError("Engine does not implement the async TraceEngine protocol")
        import uvicorn

        application = create_app(engine=engine, api_key=key)
        uvicorn.run(application, host=host, port=port, log_level="info")
    except Exception:
        # Factories may raise messages containing credentials or source payloads.
        print("Trace service could not start; verify the trusted engine factory and runtime configuration.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
