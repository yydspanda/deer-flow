"""Host control depends on a loopback-only Gateway/Next proxy trust boundary."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVE_SH = REPO_ROOT / "scripts" / "serve.sh"


def test_loopback_flag_is_accepted_before_any_service_is_stopped() -> None:
    result = subprocess.run(
        ["bash", str(SERVE_SH), "--skip-env", "--loopback-internal", "--frontend-entry=/nonexistent/entry.mjs"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "frontend entry does not exist" in result.stdout
    assert "Unknown argument" not in result.stdout
    assert "Stopping all services" not in result.stdout


@pytest.mark.parametrize("loopback", [False, True])
@pytest.mark.parametrize("mode", ["dev", "start", "preview", "custom"])
def test_loopback_internal_closes_both_internal_listeners(loopback: bool, mode: str) -> None:
    source = SERVE_SH.read_text(encoding="utf-8")
    binding = source.split("# Internal listener bindings\n", 1)[1].split("# Frontend command\n", 1)[0]
    frontend = source.split("# Frontend command\n", 1)[1].split("# Runtime path defaults.", 1)[0]
    script = f"""
set -eu
LOOPBACK_INTERNAL={str(loopback).lower()}
DEV_MODE={str(mode == "dev").lower()}
SKIP_FRONTEND_BUILD={str(mode == "start").lower()}
DEERFLOW_FRONTEND_ENTRY={"/fixture/entry.mjs" if mode == "custom" else ""}
DEERFLOW_PNPM_PYTHON={shlex.quote(sys.executable)}
{binding}
{frontend}
export GATEWAY_BIND_HOST GATEWAY_PROXY_FLAGS FRONTEND_CMD
{shlex.quote(sys.executable)} -c 'import json,os; print(json.dumps({{key: os.environ.get(key) for key in ["GATEWAY_BIND_HOST", "GATEWAY_PROXY_FLAGS", "DEERFLOW_FRONTEND_HOST", "FRONTEND_CMD"]}}))'
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin", "FORWARDED_ALLOW_IPS": "*"})
    plan = json.loads(result.stdout)
    assert plan["GATEWAY_BIND_HOST"] == ("127.0.0.1" if loopback else "0.0.0.0")
    assert plan["GATEWAY_PROXY_FLAGS"] == ("--forwarded-allow-ips=127.0.0.1,::1" if loopback else "")
    assert plan["DEERFLOW_FRONTEND_HOST"] == ("127.0.0.1" if loopback else None)
    if mode != "custom":
        assert ("--hostname 127.0.0.1" in plan["FRONTEND_CMD"]) is loopback
    assert "--host $GATEWAY_BIND_HOST" in source
    assert "$GATEWAY_PROXY_FLAGS" in source[source.index("# 1. Gateway API") :]


@pytest.mark.parametrize(
    ("connection", "forwarded", "expected"),
    [
        ("127.0.0.1", "127.0.0.1", "127.0.0.1"),
        ("127.0.0.1", "::1", "::1"),
        ("127.0.0.1", "192.0.2.25", "192.0.2.25"),
        ("127.0.0.1", "127.0.0.1, 192.0.2.25", "192.0.2.25"),
        ("127.0.0.1", "::1, 192.0.2.25", "192.0.2.25"),
        ("192.0.2.25", "127.0.0.1", "192.0.2.25"),
    ],
)
def test_pinned_loopback_proxy_trust_rejects_spoofed_local_origins(connection: str, forwarded: str, expected: str) -> None:
    observed = []

    async def application(scope, receive, send):
        observed.append(scope["client"][0])

    async def unused(*args):
        raise AssertionError("the identity probe performs no network I/O")

    middleware = ProxyHeadersMiddleware(application, trusted_hosts="127.0.0.1,::1")
    asyncio.run(middleware({"type": "http", "client": (connection, 4321), "headers": [(b"x-forwarded-for", forwarded.encode("ascii")), (b"host", b"localhost:2026"), (b"x-real-ip", b"127.0.0.1")]}, unused, unused))
    assert observed == [expected]


def test_local_nginx_keeps_the_real_connection_at_the_end_of_forwarding_chain() -> None:
    source = (REPO_ROOT / "docker/nginx/nginx.local.conf").read_text(encoding="utf-8")
    lines = [line.strip() for line in source.splitlines() if "proxy_set_header X-Forwarded-For" in line]
    assert lines
    assert set(lines) == {"proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;"}
