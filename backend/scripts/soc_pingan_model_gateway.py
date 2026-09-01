"""Run the project-owned PingAn OpenAI-compatible model gateway."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import uvicorn

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.pingan_model_gateway import create_pingan_model_gateway_app  # noqa: E402
from soc_agent.integrations.pingan.model_gateway import (  # noqa: E402
    PingAnModelGateway,
    PingAnModelGatewaySettings,
)


def main() -> None:
    settings = PingAnModelGatewaySettings.from_env()
    client = httpx.AsyncClient(
        timeout=None,
        follow_redirects=False,
        trust_env=False,
    )
    gateway = PingAnModelGateway(
        routes=(settings.route,),
        client=client,
        max_concurrency=settings.max_concurrency,
        admission_timeout_seconds=settings.admission_timeout_seconds,
        upstream_timeout_seconds=settings.upstream_timeout_seconds,
    )
    app = create_pingan_model_gateway_app(
        gateway=gateway,
        service_api_keys=settings.service_api_keys,
        max_request_bytes=settings.max_request_bytes,
    )
    try:
        uvicorn.run(
            app,
            host=settings.bind_host,
            port=settings.port,
            workers=1,
            access_log=False,
        )
    finally:
        asyncio.run(client.aclose())


if __name__ == "__main__":
    main()
