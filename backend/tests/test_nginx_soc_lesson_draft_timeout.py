"""The synchronous lesson draft can require two bounded model calls."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = (
    "docker/nginx/nginx.local.conf",
    "docker/nginx/nginx.conf",
    "deploy/helm/deer-flow/templates/configmap-nginx.yaml",
)
SELECTOR = r"~ ^/api/soc/memory/candidates/[^/]+/lesson-draft$"


def _locations(source: str) -> list[tuple[str, str]]:
    source = re.sub(r"#.*", "", source)
    return [(selector.strip(), body) for selector, body in re.findall(r"location\s+([^{}]+)\{([^{}]*)\}", source)]


def _route(locations: list[tuple[str, str]], uri: str) -> tuple[str, str]:
    """Select among these configs' flat exact, prefix and regex locations."""
    exact = next((item for item in locations if item[0] == "= " + uri), None)
    if exact:
        return exact
    prefixes = [item for item in locations if item[0].startswith(("/", "^~ ")) and uri.startswith(item[0].removeprefix("^~ "))]
    prefix = max(prefixes, key=lambda item: len(item[0].removeprefix("^~ ")))
    if prefix[0].startswith("^~ "):
        return prefix
    return next((item for item in locations if item[0].startswith("~ ") and re.search(item[0][2:], uri)), prefix)


@pytest.mark.parametrize("path", CONFIGS)
def test_lesson_draft_gets_bounded_long_timeout_without_post_retries(path: str) -> None:
    locations = _locations((REPO_ROOT / path).read_text(encoding="utf-8"))
    selector, body = _route(locations, "/api/soc/memory/candidates/MC-SYNTHETIC/lesson-draft")
    assert selector == SELECTOR
    assert "proxy_read_timeout 1800s;" in body
    assert "proxy_next_upstream off;" in body
    assert "proxy_buffering off;" in body
    assert "proxy_cache off;" in body
    assert "proxy_pass_header Set-Cookie;" in body
    assert "proxy_http_version 1.1;" in body
    assert "proxy_set_header Host $http_host;" in body
    assert "proxy_set_header X-Real-IP $remote_addr;" in body
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in body
    proto = "$scheme" if path.endswith("nginx.local.conf") else "$forwarded_proto"
    assert f"proxy_set_header X-Forwarded-Proto {proto};" in body
    upstream = "gateway" if path.endswith("nginx.local.conf") else "gateway_upstream" if path.endswith(".yaml") else "$gateway_upstream"
    assert f"proxy_pass http://{upstream};" in body
    assert "rewrite " not in body
    assert all("proxy_read_timeout 1800s;" not in other for name, other in locations if name != SELECTOR)


@pytest.mark.parametrize("path", CONFIGS)
@pytest.mark.parametrize(
    "uri",
    [
        "/api/soc/memory/candidates/MC-SYNTHETIC/review",
        "/api/soc/memory/candidates/MC-SYNTHETIC",
        "/api/soc/memory/candidates/MC-SYNTHETIC/lesson-draft/extra",
        "/api/soc/memory/candidates//lesson-draft",
        "/api/soc/dev/corpus-workbench/quick-validation",
        "/api/v1/auth/login",
    ],
)
def test_lesson_timeout_does_not_capture_other_api_routes(path: str, uri: str) -> None:
    locations = _locations((REPO_ROOT / path).read_text(encoding="utf-8"))
    selector, body = _route(locations, uri)
    assert selector == "/api/"
    assert "proxy_read_timeout" not in body
