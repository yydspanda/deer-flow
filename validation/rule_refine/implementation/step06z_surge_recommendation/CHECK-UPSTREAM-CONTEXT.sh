bash <<'SOC_UPSTREAM_CONTEXT_CHECK'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python -B - <<'PY_UPSTREAM_CONTEXT_CHECK'
"""Read advertised metadata for one already-confirmed route; never run inference."""

import argparse
import ast
import json
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SELECTED_MODEL = "Pub_DeepSeek_V4_Flash"
UPSTREAM_MODEL = "deepseek-v4-flash"
ORIGIN = "http://30.154.3.119"
BASE_URL = ORIGIN + "/v1"
HEALTH_URL = "http://127.0.0.1:8003/health"
METADATA_PATHS = (
    "/v1/models",
    "/model/info?model=deepseek-v4-flash",
    "/get_model_info",
    "/get_server_info",
)
MAX_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 5
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}\Z")
CONTEXT_FIELDS = {
    "max_model_len",
    "context_len",
    "context_length",
    "max_context_length",
    "context_window",
}
NUMBER_FIELDS = CONTEXT_FIELDS | {"max_input_tokens", "max_output_tokens", "max_tokens"}
TEXT_FIELDS = {"framework", "version", "model_version", "served_model_name"}


class CheckError(ValueError):
    """Only fixed diagnostic codes belong here, never data from a config or server."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_get(url, *, token=None):
    """No environment proxy, redirect, retry, generation request, or response dump."""
    if url == HEALTH_URL:
        if token is not None:
            raise CheckError("health_auth_forbidden")
    elif url not in {ORIGIN + path for path in METADATA_PATHS}:
        raise CheckError("destination_not_allowed")
    elif not valid_token(token):
        raise CheckError("literal_api_key_required")
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers, method="GET")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise CheckError("response_too_large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise CheckError("response_not_json") from None
    if not isinstance(data, dict):
        raise CheckError("response_not_object")
    return data


def valid_token(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 8192
        and all(32 < ord(character) < 127 for character in value)
    )


def read_text(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise CheckError("source_too_large")
    return raw.decode("utf-8-sig")


def literal_route(source):
    """Read only AST literals, including annotated assignments; execute nothing."""
    definitions = []
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(
            isinstance(target, ast.Name) and target.id == "PRD_MODEL_CONFIGS"
            for target in targets
        ):
            definitions.append(node.value)
    if len(definitions) != 1 or not isinstance(definitions[0], ast.Dict):
        raise CheckError("unique_literal_prd_config_required")
    config = definitions[0]
    if any(
        not isinstance(key, ast.Constant) or not isinstance(key.value, str)
        for key in config.keys
    ):
        raise CheckError("literal_prd_config_keys_required")
    matches = [
        value
        for key, value in zip(config.keys, config.values)
        if isinstance(key, ast.Constant) and key.value == SELECTED_MODEL
    ]
    if len(matches) != 1 or not isinstance(matches[0], ast.Dict):
        raise CheckError("unique_literal_selected_route_required")
    route = {}
    for key, value in zip(matches[0].keys, matches[0].values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise CheckError("literal_route_keys_required")
        if key.value in route:
            raise CheckError("duplicate_route_key")
        route[key.value] = value.value if isinstance(value, ast.Constant) else None
    if (
        route.get("type") != "OPENAI"
        or route.get("base_url") != BASE_URL
        or route.get("upstream_model") != UPSTREAM_MODEL
    ):
        raise CheckError("confirmed_route_mismatch")
    if not valid_token(route.get("api_key")):
        raise CheckError("literal_api_key_required")
    return route["api_key"]


def metadata_fields(container, prefix, *, secret):
    """Return bounded allowlisted scalar fields and their exact JSON source paths."""
    fields = []
    if not isinstance(container, dict):
        return fields
    for name in sorted(NUMBER_FIELDS | TEXT_FIELDS):
        value = container.get(name)
        if name in NUMBER_FIELDS:
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 < value < 10**12
            ):
                continue
            meaning = (
                "advertised_context_limit"
                if name in CONTEXT_FIELDS
                else {
                    "max_input_tokens": "advertised_input_limit",
                    "max_output_tokens": "advertised_output_limit",
                    "max_tokens": "ambiguous_token_limit_not_context_proof",
                }[name]
            )
        else:
            if (
                not isinstance(value, str)
                or not LABEL.fullmatch(value)
                or secret in value
            ):
                continue
            meaning = "advertised_label_not_framework_verification"
        fields.append({"path": prefix + [name], "value": value, "meaning": meaning})
    return fields


def filter_metadata(path, body, *, secret):
    result = {"association": "unverified", "fields": []}
    if not isinstance(body, dict):
        return result
    if path in METADATA_PATHS[:2]:
        rows = body.get("data")
        if not isinstance(rows, list):
            return result
        selector = "id" if path == "/v1/models" else "model_name"
        matching = [
            (index, row)
            for index, row in enumerate(rows)
            if isinstance(row, dict) and row.get(selector) == UPSTREAM_MODEL
        ]
        result["matching_rows"] = len(matching)
        if not matching:
            result["association"] = "selected_model_not_listed"
            return result
        result["association"] = "selected_model_advertised"
        for index, row in matching:
            prefix = ["data", index]
            result["fields"].extend(metadata_fields(row, prefix, secret=secret))
            result["fields"].extend(
                metadata_fields(
                    row.get("model_info"), prefix + ["model_info"], secret=secret
                )
            )
            params = row.get("litellm_params")
            model = params.get("model") if isinstance(params, dict) else None
            if (
                isinstance(model, str)
                and LABEL.fullmatch(model)
                and secret not in model
            ):
                result["fields"].append(
                    {
                        "path": prefix + ["litellm_params", "model"],
                        "value": model,
                        "meaning": "advertised_upstream_model_label",
                    }
                )
        return result
    containers = [([], body)] + [
        ([name], body.get(name)) for name in ("model_info", "server_args")
    ]
    served_names = [
        container["served_model_name"]
        for _, container in containers
        if isinstance(container, dict) and "served_model_name" in container
    ]
    if served_names:
        if any(
            name != UPSTREAM_MODEL and name != [UPSTREAM_MODEL] for name in served_names
        ):
            result["association"] = "served_model_mismatch"
            return result
        result["association"] = "selected_model_advertised"
    else:
        result["association"] = "service_metadata_model_association_unverified"
    for prefix, container in containers:
        result["fields"].extend(metadata_fields(container, prefix, secret=secret))
    return result


def failure(error):
    if isinstance(error, HTTPError):
        result = {"status": "http_error", "http_status": error.code}
        error.close()
        return result
    if isinstance(error, CheckError):
        return {"status": str(error)}
    return {"status": "read_failed"}


def inspect(root, *, get=read_get):
    result = {
        "check": "rule_refine_upstream_metadata",
        "read_only": True,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "application_code_updated": False,
        "capacity_probe_performed": False,
        "evidence_scope": "advertised_metadata_only",
        "actual_context_capacity_verified": False,
        "server_deployment_configuration_required": True,
        "selected_model": SELECTED_MODEL,
        "upstream_model": UPSTREAM_MODEL,
        "confirmed_endpoint": BASE_URL,
        "loopback_get_calls": 0,
        "remote_get_calls": 0,
        "context_capacity_status": "unknown",
        "endpoints": [],
    }
    try:
        config = json.loads(read_text(root / "config/rule_refine.analysis.local.json"))
        if not isinstance(config, dict) or config.get("model") != SELECTED_MODEL:
            raise CheckError("selected_model_mismatch")
        secret = literal_route(read_text(root / "llm_service/openai_completion.py"))
    except Exception as error:
        result.update(status="local_route_not_verified", local_route=failure(error))
        return result
    try:
        result["loopback_get_calls"] += 1
        health = get(HEALTH_URL)
        upstreams = health.get("upstreams") if isinstance(health, dict) else None
        route = upstreams.get(SELECTED_MODEL) if isinstance(upstreams, dict) else None
        if (
            not isinstance(route, dict)
            or route.get("type") != "OPENAI"
            or route.get("base_url") != BASE_URL
        ):
            raise CheckError("running_route_mismatch")
    except Exception as error:
        result.update(status="running_route_not_verified", running_route=failure(error))
        return result
    result["running_route"] = {
        "status": "matches_confirmed_endpoint_and_type",
        "upstream_deployment_identity_verified": False,
    }
    denied = False
    for path in METADATA_PATHS:
        row = {"path": path}
        result["endpoints"].append(row)
        if denied:
            row["status"] = "skipped_after_auth_denied"
            continue
        result["remote_get_calls"] += 1
        try:
            body = get(ORIGIN + path, token=secret)
            row.update(
                status="metadata_read", **filter_metadata(path, body, secret=secret)
            )
        except Exception as error:
            row.update(failure(error))
            denied = row.get("http_status") in {401, 403}
    context_rows = [
        row
        for row in result["endpoints"]
        if any(
            field["meaning"] == "advertised_context_limit"
            for field in row.get("fields", [])
        )
    ]
    if any(row["association"] == "selected_model_advertised" for row in context_rows):
        result["context_capacity_status"] = (
            "selected_model_context_metadata_observed_not_capacity_tested"
        )
    elif context_rows:
        result["context_capacity_status"] = (
            "service_context_metadata_observed_model_association_unverified"
        )
    result["status"] = (
        "metadata_observed"
        if any(row.get("fields") for row in result["endpoints"])
        else "metadata_unavailable"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "sec_know_model")
    args = parser.parse_args()
    print(json.dumps(inspect(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

PY_UPSTREAM_CONTEXT_CHECK
SOC_UPSTREAM_CONTEXT_CHECK
