"""Prepare the private PingAn model-gateway profile from reviewed legacy source."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

LEGACY_MODEL_SOURCE_PATH = Path("validation/original_works/sec_know_model/llm_service/openai_completion.py")
LEGACY_ROOT_CONFIG_PATH = Path("validation/original_works/sec_know_model/util/root_config.py")
LEGACY_MODEL_GATEWAY_ENV_PATH = Path(".env.soc-dev.local")
LEGACY_MODEL_GATEWAY_KEY_PATH = Path(".secrets/eagw-private-key.der")
LEGACY_MODEL_GATEWAY_ENVIRONMENT = "stg"
LEGACY_MODEL_CONFIG_NAME = "DeepSeek_V4_Flash"
LEGACY_MODEL_GATEWAY_PROFILE_SCHEMA_VERSION = "soc.pingan_legacy_model_gateway_profile.v1"

_BLOCK_BEGIN = "# BEGIN SOC PINGAN LEGACY MODEL GATEWAY PROFILE"
_BLOCK_END = "# END SOC PINGAN LEGACY MODEL GATEWAY PROFILE"
_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=")
_MANAGED_KEYS = (
    "PINGAN_MODEL_GATEWAY_BASE_URL",
    "PINGAN_MODEL_GATEWAY_API_KEY",
    "PINGAN_MODEL_GATEWAY_MODEL",
    "PINGAN_MODEL_GATEWAY_SMOKE_THINKING_ENABLED",
    "PINGAN_MODEL_GATEWAY_SMOKE_REASONING_EFFORT",
    "SOC_PINGAN_MODEL_GATEWAY_HOST",
    "SOC_PINGAN_MODEL_GATEWAY_ENABLED",
    "SOC_PINGAN_MODEL_GATEWAY_PORT",
    "SOC_PINGAN_MODEL_GATEWAY_API_KEYS",
    "SOC_PINGAN_MODEL_GATEWAY_MODEL_ALIAS",
    "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_MODEL",
    "SOC_PINGAN_MODEL_GATEWAY_PROVIDER",
    "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_BASE_URL",
    "SOC_PINGAN_MODEL_GATEWAY_ALLOWED_HOSTS",
    "SOC_PINGAN_MODEL_GATEWAY_ALLOW_INSECURE_HTTP",
    "SOC_PINGAN_MODEL_GATEWAY_APP_KEY",
    "SOC_PINGAN_MODEL_GATEWAY_APP_SECRET",
    "SOC_PINGAN_MODEL_GATEWAY_SCENE_ID",
    "SOC_PINGAN_MODEL_GATEWAY_OPENAPI_CODE",
    "SOC_PINGAN_MODEL_GATEWAY_OPENAPI_CREDENTIAL",
    "SOC_PINGAN_MODEL_GATEWAY_RSA_PRIVATE_KEY_FILE",
    "SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY",
    "SOC_PINGAN_MODEL_GATEWAY_ADMISSION_TIMEOUT_SECONDS",
    "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_TIMEOUT_SECONDS",
    "SOC_PINGAN_MODEL_GATEWAY_MAX_REQUEST_BYTES",
    "SOC_ANALYZER_MODE",
    "SOC_LLM_MODEL",
    "SOC_VALIDATION_MODEL",
    "SOC_LLM_MAX_CONCURRENCY",
    "SOC_LLM_CALL_TIMEOUT_SECONDS",
    "SOC_LLM_SENSITIVE_EVIDENCE_MODE",
    "SOC_LLM_OUTPUT_RETRY_ATTEMPTS",
    "SOC_LLM_OUTPUT_FALLBACK_MODEL",
    "SOC_ROLE_VERIFIER_ENABLED",
    "SOC_ROLE_VERIFIER_MODEL",
    "SOC_ROLE_VERIFIER_MIN_CONFIDENCE",
    "SOC_PINGAN_COMPAT_HOST",
    "SOC_PINGAN_COMPAT_ENABLED",
    "SOC_PINGAN_COMPAT_PORT",
    "SOC_PINGAN_COMPAT_APP_KEYS_JSON",
    "SOC_PINGAN_COMPAT_AUTO_MIGRATE",
    "SOC_PINGAN_COMPAT_MAX_REQUEST_BYTES",
    "SOC_PINGAN_COMPAT_SMOKE_BASE_URL",
    "SOC_PINGAN_COMPAT_SMOKE_TIMEOUT_SECONDS",
    "SOC_PINGAN_COMPAT_SMOKE_POLL_INTERVAL_SECONDS",
    "SOC_PINGAN_COMPAT_SMOKE_MAX_RESPONSE_BYTES",
    "SOC_PINGAN_LEGACY_QUEUE_TTL_SECONDS",
    "SOC_PINGAN_LEGACY_LIFECYCLE_MODE",
    "SOC_PINGAN_LEGACY_CALLBACK_MODE",
    "SOC_PINGAN_LEGACY_WORKER_CONCURRENCY",
    "SOC_PINGAN_LEGACY_POLL_INTERVAL_SECONDS",
    "SOC_PINGAN_LEGACY_WORKER_LEASE_SECONDS",
    "SOC_PINGAN_LEGACY_WORKER_MAX_ATTEMPTS",
    "SOC_PINGAN_LEGACY_WORKER_RETRY_BACKOFF_SECONDS",
    "SOC_PINGAN_LEGACY_CALLBACK_LEASE_SECONDS",
    "SOC_PINGAN_LEGACY_CALLBACK_MAX_ATTEMPTS",
    "SOC_PINGAN_LEGACY_CALLBACK_RETRY_BACKOFF_SECONDS",
    "SOC_PINGAN_LEGACY_WORKER_AUTO_MIGRATE",
    "SOC_PINGAN_ENV",
)
_REMOVED_KEYS = frozenset(
    {
        "PINGAN_LITELLM_BASE_URL",
        "PINGAN_LITELLM_API_KEY",
        "PINGAN_LITELLM_MODEL",
    }
)


class PingAnLegacyModelGatewayProfileError(ValueError):
    """Raised when legacy source cannot produce one safe gateway profile."""


@dataclass(frozen=True)
class PingAnLegacyModelGatewayProfile:
    """Minimal EAGW and old-ingress values extracted without importing old code."""

    environment: str
    model_config_name: str
    base_url: str
    allowed_host: str
    scene_id: str
    openapi_code: str
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    openapi_credential: str = field(repr=False)
    rsa_private_key_der: bytes = field(repr=False)
    compat_app_keys: dict[str, str] = field(repr=False)


def load_legacy_model_gateway_profile(
    model_source_path: Path,
    *,
    root_config_path: Path,
    environment: str = LEGACY_MODEL_GATEWAY_ENVIRONMENT,
    model_config_name: str = LEGACY_MODEL_CONFIG_NAME,
) -> PingAnLegacyModelGatewayProfile:
    """Statically read one reviewed model route and old app-key mapping."""

    normalized_environment = environment.strip().lower()
    if normalized_environment != "stg":
        raise PingAnLegacyModelGatewayProfileError("only the reviewed STG legacy model profile may be prepared")
    model_assignments = _safe_assignments(model_source_path)
    root_assignments = _safe_assignments(root_config_path)
    model_configs = model_assignments.get("STG_MODEL_CONFIGS")
    if not isinstance(model_configs, dict):
        raise PingAnLegacyModelGatewayProfileError("legacy model source omitted STG_MODEL_CONFIGS")
    model_config = model_configs.get(model_config_name)
    if not isinstance(model_config, dict):
        raise PingAnLegacyModelGatewayProfileError(f"legacy STG profile omitted model config {model_config_name}")
    if str(model_config.get("type", "")).strip().upper() != "EAGW":
        raise PingAnLegacyModelGatewayProfileError("reviewed legacy model profile must use EAGW")

    base_url = _required_string(model_config, "base_url").rstrip("/")
    parsed_url = urlsplit(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise PingAnLegacyModelGatewayProfileError("legacy EAGW base URL must be an HTTP(S) origin")
    key_hex = _required_string(model_config, "rsa_private_key_hex")
    key_der = _normalize_private_key(key_hex)
    openapi_code = model_assignments.get("OPENAPI_CHAT_CODE")
    if not isinstance(openapi_code, str) or not openapi_code.strip():
        raise PingAnLegacyModelGatewayProfileError("legacy model source omitted OPENAPI_CHAT_CODE")
    app_keys = root_assignments.get("APP_KEY")
    if not isinstance(app_keys, dict) or not app_keys:
        raise PingAnLegacyModelGatewayProfileError("legacy root config omitted APP_KEY")
    compat_app_keys = {str(name).strip(): str(value).strip() for name, value in app_keys.items() if isinstance(name, str) and isinstance(value, str) and name.strip() and value.strip()}
    if len(compat_app_keys) != len(app_keys):
        raise PingAnLegacyModelGatewayProfileError("legacy APP_KEY must contain only non-empty string pairs")

    return PingAnLegacyModelGatewayProfile(
        environment=normalized_environment,
        model_config_name=model_config_name,
        base_url=base_url,
        allowed_host=parsed_url.hostname.lower(),
        scene_id=_required_string(model_config, "scene_id"),
        openapi_code=openapi_code.strip(),
        app_key=_required_string(model_config, "app_key"),
        app_secret=_required_string(model_config, "app_secret"),
        openapi_credential=_required_string(model_config, "openapi_credential"),
        rsa_private_key_der=key_der,
        compat_app_keys=compat_app_keys,
    )


def prepare_legacy_model_gateway_env(
    *,
    repo_root: Path,
    model_source_path: Path | None = None,
    root_config_path: Path | None = None,
    env_path: Path | None = None,
    key_path: Path | None = None,
    environment: str = LEGACY_MODEL_GATEWAY_ENVIRONMENT,
    model_config_name: str = LEGACY_MODEL_CONFIG_NAME,
    apply: bool = False,
) -> dict[str, Any]:
    """Build or atomically update private gateway files without printing secrets."""

    root = repo_root.expanduser().resolve()
    model_source = (model_source_path or (root / LEGACY_MODEL_SOURCE_PATH)).expanduser().resolve()
    root_config = (root_config_path or (root / LEGACY_ROOT_CONFIG_PATH)).expanduser().resolve()
    target_env = (env_path or (root / LEGACY_MODEL_GATEWAY_ENV_PATH)).expanduser().resolve()
    target_key = (key_path or (root / LEGACY_MODEL_GATEWAY_KEY_PATH)).expanduser().resolve()
    profile = load_legacy_model_gateway_profile(
        model_source,
        root_config_path=root_config,
        environment=environment,
        model_config_name=model_config_name,
    )
    current = target_env.read_text(encoding="utf-8") if target_env.is_file() else ""
    current_values = _shell_assignment_values(current)
    local_api_key = current_values.get("PINGAN_MODEL_GATEWAY_API_KEY", "").strip() or current_values.get("PINGAN_LITELLM_API_KEY", "").strip()
    if not local_api_key or _looks_like_placeholder(local_api_key):
        raise PingAnLegacyModelGatewayProfileError("existing private env must provide PINGAN_MODEL_GATEWAY_API_KEY or legacy PINGAN_LITELLM_API_KEY")
    values = _profile_env_values(
        profile,
        local_api_key=local_api_key,
        key_path=_portable_key_path(target_key, root),
    )
    rendered, removed_keys = _rewrite_env(current, values)
    env_changed = rendered != current
    key_changed = not target_key.is_file() or target_key.read_bytes() != (profile.rsa_private_key_der)

    if apply:
        if env_changed:
            _write_private_file(target_env, rendered.encode("utf-8"))
        elif target_env.is_file():
            target_env.chmod(0o600)
        if key_changed:
            _write_private_file(target_key, profile.rsa_private_key_der)
        elif target_key.is_file():
            target_key.chmod(0o600)

    return {
        "schema_version": LEGACY_MODEL_GATEWAY_PROFILE_SCHEMA_VERSION,
        "model_source_path": _display_path(model_source, root),
        "model_source_sha256": _sha256(model_source),
        "root_config_path": _display_path(root_config, root),
        "root_config_sha256": _sha256(root_config),
        "env_path": _display_path(target_env, root),
        "key_path": _display_path(target_key, root),
        "key_sha256": hashlib.sha256(profile.rsa_private_key_der).hexdigest(),
        "environment": profile.environment,
        "model_config_name": profile.model_config_name,
        "base_url": profile.base_url,
        "allowed_host": profile.allowed_host,
        "scene_id": profile.scene_id,
        "openapi_code": profile.openapi_code,
        "credential_present": bool(profile.app_key and profile.app_secret and profile.openapi_credential and local_api_key),
        "compatibility_key_present": bool(profile.compat_app_keys),
        "updated_keys": list(_MANAGED_KEYS),
        "removed_keys": sorted(removed_keys),
        "env_changed": env_changed,
        "key_changed": key_changed,
        "applied": apply,
        "secret_in_output": False,
    }


def _safe_assignments(source_path: Path) -> dict[str, Any]:
    try:
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
    except SyntaxError as exc:
        raise PingAnLegacyModelGatewayProfileError(f"legacy source is not valid Python: {source_path.name}") from exc

    assignments: dict[str, Any] = {}
    for statement in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        try:
            assignments[target.id] = _safe_eval(value, assignments)
        except PingAnLegacyModelGatewayProfileError:
            continue
    return assignments


def _safe_eval(node: ast.expr, assignments: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in assignments:
        return assignments[node.id]
    if isinstance(node, ast.Dict):
        if any(key is None for key in node.keys):
            raise PingAnLegacyModelGatewayProfileError("dictionary expansion is outside the static profile contract")
        return {_safe_eval(key, assignments): _safe_eval(value, assignments) for key, value in zip(node.keys, node.values, strict=True) if key is not None}
    if isinstance(node, ast.List):
        return [_safe_eval(item, assignments) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_eval(item, assignments) for item in node.elts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _safe_eval(node.operand, assignments)
        if not isinstance(operand, (int, float)):
            raise PingAnLegacyModelGatewayProfileError("unary expression is not a numeric literal")
        return operand if isinstance(node.op, ast.UAdd) else -operand
    raise PingAnLegacyModelGatewayProfileError(f"unsupported legacy expression: {type(node).__name__}")


def _required_string(values: dict[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, (str, int)) or not str(value).strip():
        raise PingAnLegacyModelGatewayProfileError(f"legacy model config omitted {key}")
    return str(value).strip()


def _normalize_private_key(key_hex: str) -> bytes:
    try:
        raw = bytes.fromhex(key_hex.strip())
        key = serialization.load_der_private_key(raw, password=None)
    except (TypeError, ValueError) as exc:
        raise PingAnLegacyModelGatewayProfileError("legacy EAGW RSA private key is invalid") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise PingAnLegacyModelGatewayProfileError("legacy EAGW private key must be RSA")
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _profile_env_values(
    profile: PingAnLegacyModelGatewayProfile,
    *,
    local_api_key: str,
    key_path: str,
) -> dict[str, str]:
    return {
        "PINGAN_MODEL_GATEWAY_BASE_URL": "http://127.0.0.1:4001/v1",
        "PINGAN_MODEL_GATEWAY_API_KEY": local_api_key,
        "PINGAN_MODEL_GATEWAY_MODEL": "deepseek-v4-flash",
        "PINGAN_MODEL_GATEWAY_SMOKE_THINKING_ENABLED": "true",
        "PINGAN_MODEL_GATEWAY_SMOKE_REASONING_EFFORT": "high",
        "SOC_PINGAN_MODEL_GATEWAY_HOST": "127.0.0.1",
        "SOC_PINGAN_MODEL_GATEWAY_ENABLED": "true",
        "SOC_PINGAN_MODEL_GATEWAY_PORT": "4001",
        "SOC_PINGAN_MODEL_GATEWAY_API_KEYS": local_api_key,
        "SOC_PINGAN_MODEL_GATEWAY_MODEL_ALIAS": "deepseek-v4-flash",
        "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_MODEL": "deepseek-v4-flash-0731",
        "SOC_PINGAN_MODEL_GATEWAY_PROVIDER": "eagw",
        "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_BASE_URL": profile.base_url,
        "SOC_PINGAN_MODEL_GATEWAY_ALLOWED_HOSTS": profile.allowed_host,
        "SOC_PINGAN_MODEL_GATEWAY_ALLOW_INSECURE_HTTP": str(profile.base_url.startswith("http://")).lower(),
        "SOC_PINGAN_MODEL_GATEWAY_APP_KEY": profile.app_key,
        "SOC_PINGAN_MODEL_GATEWAY_APP_SECRET": profile.app_secret,
        "SOC_PINGAN_MODEL_GATEWAY_SCENE_ID": profile.scene_id,
        "SOC_PINGAN_MODEL_GATEWAY_OPENAPI_CODE": profile.openapi_code,
        "SOC_PINGAN_MODEL_GATEWAY_OPENAPI_CREDENTIAL": (profile.openapi_credential),
        "SOC_PINGAN_MODEL_GATEWAY_RSA_PRIVATE_KEY_FILE": key_path,
        "SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY": "1",
        "SOC_PINGAN_MODEL_GATEWAY_ADMISSION_TIMEOUT_SECONDS": "5",
        "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_TIMEOUT_SECONDS": "600",
        "SOC_PINGAN_MODEL_GATEWAY_MAX_REQUEST_BYTES": "2000000",
        "SOC_ANALYZER_MODE": "llm",
        "SOC_LLM_MODEL": "deepseek-v4-flash",
        "SOC_VALIDATION_MODEL": "deepseek-v4-flash",
        "SOC_LLM_MAX_CONCURRENCY": "1",
        "SOC_LLM_CALL_TIMEOUT_SECONDS": "600",
        "SOC_LLM_SENSITIVE_EVIDENCE_MODE": "full",
        "SOC_LLM_OUTPUT_RETRY_ATTEMPTS": "1",
        "SOC_LLM_OUTPUT_FALLBACK_MODEL": "",
        "SOC_ROLE_VERIFIER_ENABLED": "false",
        "SOC_ROLE_VERIFIER_MODEL": "deepseek-v4-pro",
        "SOC_ROLE_VERIFIER_MIN_CONFIDENCE": "0.65",
        "SOC_PINGAN_COMPAT_HOST": "0.0.0.0",
        "SOC_PINGAN_COMPAT_ENABLED": "true",
        "SOC_PINGAN_COMPAT_PORT": "8090",
        "SOC_PINGAN_COMPAT_APP_KEYS_JSON": json.dumps(
            profile.compat_app_keys,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "SOC_PINGAN_COMPAT_AUTO_MIGRATE": "true",
        "SOC_PINGAN_COMPAT_MAX_REQUEST_BYTES": "5000000",
        "SOC_PINGAN_COMPAT_SMOKE_BASE_URL": "http://127.0.0.1:8090",
        "SOC_PINGAN_COMPAT_SMOKE_TIMEOUT_SECONDS": "900",
        "SOC_PINGAN_COMPAT_SMOKE_POLL_INTERVAL_SECONDS": "1",
        "SOC_PINGAN_COMPAT_SMOKE_MAX_RESPONSE_BYTES": "5000000",
        "SOC_PINGAN_LEGACY_QUEUE_TTL_SECONDS": "1800",
        "SOC_PINGAN_LEGACY_LIFECYCLE_MODE": "fake",
        "SOC_PINGAN_LEGACY_CALLBACK_MODE": "fake",
        "SOC_PINGAN_LEGACY_WORKER_CONCURRENCY": "1",
        "SOC_PINGAN_LEGACY_POLL_INTERVAL_SECONDS": "1",
        "SOC_PINGAN_LEGACY_WORKER_LEASE_SECONDS": "900",
        "SOC_PINGAN_LEGACY_WORKER_MAX_ATTEMPTS": "3",
        "SOC_PINGAN_LEGACY_WORKER_RETRY_BACKOFF_SECONDS": "5",
        "SOC_PINGAN_LEGACY_CALLBACK_LEASE_SECONDS": "60",
        "SOC_PINGAN_LEGACY_CALLBACK_MAX_ATTEMPTS": "8",
        "SOC_PINGAN_LEGACY_CALLBACK_RETRY_BACKOFF_SECONDS": "5",
        "SOC_PINGAN_LEGACY_WORKER_AUTO_MIGRATE": "false",
        "SOC_PINGAN_ENV": "dev",
    }


def _shell_assignment_values(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        match = _ENV_ASSIGNMENT.match(line)
        if match is None:
            continue
        name = match.group("name")
        raw_value = line[match.end() :].strip()
        try:
            parsed = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            continue
        if len(parsed) == 1:
            values[name] = parsed[0]
    return values


def _rewrite_env(
    current: str,
    values: dict[str, str],
) -> tuple[str, set[str]]:
    retained: list[str] = []
    removed: set[str] = set()
    inside_generated_block = False
    for line in current.splitlines():
        if line.strip() == _BLOCK_BEGIN:
            inside_generated_block = True
            continue
        if line.strip() == _BLOCK_END:
            inside_generated_block = False
            continue
        if inside_generated_block:
            continue
        match = _ENV_ASSIGNMENT.match(line)
        name = match.group("name") if match else None
        if name in values or name in _REMOVED_KEYS:
            if name in _REMOVED_KEYS:
                removed.add(name)
            continue
        retained.append(line)

    while retained and not retained[-1].strip():
        retained.pop()
    if retained:
        retained.append("")
    retained.extend(
        [
            _BLOCK_BEGIN,
            "# Statically prepared from reviewed legacy STG EAGW and ZEUS profiles.",
            "# The project owns ports 4001/8090; lifecycle/callback stay fake until live acceptance.",
            *(_render_assignment(name, values[name]) for name in _MANAGED_KEYS),
            _BLOCK_END,
        ]
    )
    return "\n".join(retained) + "\n", removed


def _render_assignment(name: str, value: str) -> str:
    if value.startswith("${SOC_REPO_ROOT}/"):
        escaped = value.replace('"', '\\"')
        return f'export {name}="{escaped}"'
    return f"export {name}={shlex.quote(value)}"


def _portable_key_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PingAnLegacyModelGatewayProfileError("model-gateway key path must remain inside the repository") from exc
    return "${SOC_REPO_ROOT}/" + relative.as_posix()


def _write_private_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip()
    return not normalized or (normalized.startswith("<") and normalized.endswith(">")) or normalized.lower() in {"changeme", "todo", "replace-me"}


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sanitized_profile_json(report: dict[str, Any]) -> str:
    """Serialize only the public preparation report."""

    return json.dumps(report, ensure_ascii=False, indent=2)


__all__ = [
    "LEGACY_MODEL_CONFIG_NAME",
    "LEGACY_MODEL_GATEWAY_ENV_PATH",
    "LEGACY_MODEL_GATEWAY_ENVIRONMENT",
    "LEGACY_MODEL_GATEWAY_KEY_PATH",
    "LEGACY_MODEL_GATEWAY_PROFILE_SCHEMA_VERSION",
    "LEGACY_MODEL_SOURCE_PATH",
    "LEGACY_ROOT_CONFIG_PATH",
    "PingAnLegacyModelGatewayProfile",
    "PingAnLegacyModelGatewayProfileError",
    "load_legacy_model_gateway_profile",
    "prepare_legacy_model_gateway_env",
    "sanitized_profile_json",
]
