"""Read only the selected model's local adapter route; never probe model capacity."""

import ast
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HEALTH_URL = "http://127.0.0.1:8003/health"
MAX_BYTES = 1024 * 1024
DEFAULT_MODEL = "Pub_DeepSeek_V4_Flash"
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
SETTINGS = {
    "drop_params",
    "max_chat_completion_tokens",
    "max_tokens",
    "max_input_tokens",
    "context_window",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_get(url):
    if url != HEALTH_URL:
        raise ValueError("only_fixed_loopback_health_allowed")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    with opener.open(request, timeout=3) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("health_response_too_large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("health_object_required")
    return data


def endpoint(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    if any(character.isspace() or ord(character) < 32 for character in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname:
            return None
        host = url.hostname
        if ":" in host:
            host = "[" + host + "]"
        port = url.port
        if port is not None:
            host += ":" + str(port)
        return urlunsplit((url.scheme, host, url.path, "", ""))
    except ValueError:
        return None


def safe_label(value):
    return value if isinstance(value, str) and LABEL.fullmatch(value) else None


def source(path, output):
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            output.update(status="file_too_large")
            return None
        output.update(status="read", sha256=hashlib.sha256(raw).hexdigest())
        return raw.decode("utf-8-sig")
    except FileNotFoundError:
        output.update(status="missing")
    except (OSError, UnicodeError):
        output.update(status="unreadable")
    return None


def literal_routes(text, selected):
    tree = ast.parse(text)
    candidates = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(
            node.value, ast.Dict
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        names = [
            name for name in names if name in {"STG_MODEL_CONFIGS", "PRD_MODEL_CONFIGS"}
        ]
        if not names:
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if not isinstance(key, ast.Constant) or key.value != selected:
                continue
            fields = {}
            if isinstance(value, ast.Dict):
                for field, item in zip(value.keys, value.values):
                    if (
                        isinstance(field, ast.Constant)
                        and field.value in {"type", "base_url", "upstream_model"}
                        and isinstance(item, ast.Constant)
                    ):
                        fields[field.value] = item.value
            for name in names:
                candidates.append(
                    {
                        "config": name,
                        "line": value.lineno,
                        "type": safe_label(fields.get("type")),
                        "upstream_model": safe_label(fields.get("upstream_model")),
                        "endpoint": endpoint(fields.get("base_url")),
                    }
                )
    return candidates


def yaml_settings(text):
    """Read only unambiguous scalar lines inside top-level litellm_settings."""
    result = {}
    active = False
    child_indent = None
    seen = set()
    for line in text.splitlines():
        if re.fullmatch(r"litellm_settings:\s*(?:#.*)?", line):
            active = True
            child_indent = None
            continue
        if line and not line[0].isspace() and not line.startswith("#"):
            active = False
        if not active:
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue
        match = re.fullmatch(
            r"[ \t]+([A-Za-z_]+):\s*(true|false|[0-9]+)\s*(?:#.*)?", line
        )
        if match and match[1] in SETTINGS:
            if match[1] in seen:
                result.pop(match[1], None)
                continue
            seen.add(match[1])
            value = match[2]
            result[match[1]] = (
                value == "true" if value in {"true", "false"} else int(value)
            )
    return result


def inspect(root, *, get=read_get):
    result = {
        "check": "rule_refine_context_route",
        "read_only": True,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "application_code_updated": False,
        "capacity_probe_performed": False,
        "code_on_disk_is_not_runtime_proof": True,
        "server_deployment_configuration_required": True,
        "source_files": {},
        "running_adapter": {"status": "unknown"},
        "disk_literal_matches_running_endpoint": None,
    }
    texts = {}
    for relative in (
        "config/rule_refine.analysis.local.json",
        "llm_service/openai_completion.py",
        "llm_service/litellm_proxy_config.local.yaml",
    ):
        state = {}
        texts[relative] = source(root / relative, state)
        result["source_files"][relative] = state
    config = texts["config/rule_refine.analysis.local.json"]
    selected = DEFAULT_MODEL
    result["selected_model_basis"] = "default"
    if config is not None:
        try:
            value = json.loads(config)
            selected = (
                safe_label(value.get("model")) if isinstance(value, dict) else None
            )
        except ValueError:
            selected = None
        if selected is None:
            result.update(status="invalid_selected_model", selected_model=None)
            return result
        result["selected_model_basis"] = "local_analysis_file"
    result["selected_model"] = selected
    try:
        result["installed_litellm_version"] = importlib.metadata.version("litellm")
    except importlib.metadata.PackageNotFoundError:
        result["installed_litellm_version"] = None
    code = texts["llm_service/openai_completion.py"]
    candidates = []
    result["disk_routes_status"] = "unavailable"
    if code is not None:
        try:
            candidates = literal_routes(code, selected)
            result["disk_routes_status"] = "literal_candidates_only"
        except (SyntaxError, ValueError):
            result["disk_routes_status"] = "unparseable"
    result["disk_route_candidates"] = candidates
    yaml = texts["llm_service/litellm_proxy_config.local.yaml"]
    result["local_yaml_settings"] = {
        "values": yaml_settings(yaml) if yaml is not None else {},
        "basis": "local_file_only_not_verified_running_configuration",
    }
    try:
        health = get(HEALTH_URL)
        upstreams = health.get("upstreams") if isinstance(health, dict) else None
        route = upstreams.get(selected) if isinstance(upstreams, dict) else None
        if not isinstance(route, dict):
            result["running_adapter"] = {"status": "selected_route_not_reported"}
        else:
            runtime_endpoint = endpoint(route.get("base_url"))
            runtime_type = safe_label(route.get("type"))
            result["running_adapter"] = {
                "status": "selected_route_reported"
                if runtime_endpoint
                else "unknown_endpoint",
                "type": runtime_type,
                "endpoint": runtime_endpoint,
                "basis": "loopback_adapter_health_not_upstream_deployment_metadata",
            }
            known = [
                row["endpoint"] for row in candidates if row["endpoint"] is not None
            ]
            if runtime_endpoint is not None and known:
                result["disk_literal_matches_running_endpoint"] = (
                    runtime_endpoint in known
                )
    except HTTPError as error:
        result["running_adapter"] = {
            "status": "health_http_error",
            "http_status": error.code,
        }
        error.close()
    except Exception as error:
        result["running_adapter"] = {
            "status": "health_unavailable",
            "error_type": type(error).__name__,
        }
    result["status"] = (
        "route_observed_context_capacity_unknown"
        if result["running_adapter"]["status"] == "selected_route_reported"
        else "runtime_route_unknown"
    )
    return result


def main():
    print(
        json.dumps(
            inspect(Path.home() / "sec_know_model"), ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
