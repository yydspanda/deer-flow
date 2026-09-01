"""PingAn-owned OpenAI-compatible model gateway primitives.

The public contract is vendor neutral, while EAGW signing and scene routing
remain isolated in the PingAn integration. Credentials are accepted only from
the caller/wiring layer and are never included in response metadata.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class PingAnModelProvider(StrEnum):
    """Supported upstream transport profiles."""

    OPENAI = "openai"
    EAGW = "eagw"


class PingAnModelGatewayError(RuntimeError):
    """Base class for bounded, client-safe gateway failures."""

    code = "model_gateway_error"
    http_status = 502


class PingAnModelGatewayRequestError(PingAnModelGatewayError):
    code = "invalid_request"
    http_status = 400


class PingAnModelGatewayStreamingNotEnabledError(PingAnModelGatewayRequestError):
    code = "streaming_not_enabled"


class PingAnModelGatewayBusyError(PingAnModelGatewayError):
    code = "model_capacity_busy"
    http_status = 429


class PingAnModelGatewayUpstreamTimeoutError(PingAnModelGatewayError):
    code = "upstream_timeout"
    http_status = 504


class PingAnModelGatewayUpstreamError(PingAnModelGatewayError):
    code = "upstream_error"
    http_status = 502


@dataclass(frozen=True)
class PingAnModelRoute:
    """One public alias mapped to one governed upstream model."""

    alias: str
    upstream_model: str
    provider: PingAnModelProvider
    base_url: str
    allowed_hosts: tuple[str, ...]
    api_key: str | None = field(default=None, repr=False)
    app_key: str | None = field(default=None, repr=False)
    app_secret: str | None = field(default=None, repr=False)
    scene_id: str | None = None
    openapi_code: str | None = None
    openapi_credential: str | None = field(default=None, repr=False)
    rsa_private_key_hex: str | None = field(default=None, repr=False)
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        alias = self.alias.strip()
        upstream_model = self.upstream_model.strip()
        base_url = self.base_url.strip().rstrip("/")
        allowed_hosts = tuple(dict.fromkeys(host.strip().lower() for host in self.allowed_hosts if host.strip()))
        parsed = urlsplit(base_url)
        if not alias or not upstream_model:
            raise ValueError("model alias and upstream_model are required")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("model base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("model base_url must not contain credentials")
        if parsed.scheme == "http" and not self.allow_insecure_http:
            raise ValueError("plain HTTP model upstream requires allow_insecure_http=true")
        if parsed.hostname.lower() not in allowed_hosts:
            raise ValueError("model upstream hostname must be present in allowed_hosts")
        if self.provider is PingAnModelProvider.OPENAI and not self.api_key:
            raise ValueError("OpenAI-compatible model route requires api_key")
        if self.provider is PingAnModelProvider.EAGW:
            missing = [
                name
                for name, value in (
                    ("app_key", self.app_key),
                    ("app_secret", self.app_secret),
                    ("scene_id", self.scene_id),
                    ("openapi_code", self.openapi_code),
                    ("openapi_credential", self.openapi_credential),
                    ("rsa_private_key_hex", self.rsa_private_key_hex),
                )
                if not value
            ]
            if missing:
                raise ValueError("EAGW model route is missing: " + ", ".join(missing))
            _load_rsa_private_key(str(self.rsa_private_key_hex))
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "upstream_model", upstream_model)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "allowed_hosts", allowed_hosts)


@dataclass(frozen=True)
class PingAnModelGatewayResponse:
    """Provider response plus secret-free operational metadata."""

    status_code: int
    body: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PingAnModelGatewaySettings:
    """Process settings resolved from the operator-owned private overlay."""

    route: PingAnModelRoute
    service_api_keys: tuple[str, ...] = field(repr=False)
    bind_host: str = "127.0.0.1"
    port: int = 4001
    max_request_bytes: int = 2_000_000
    max_concurrency: int = 1
    admission_timeout_seconds: float = 5.0
    upstream_timeout_seconds: float = 600.0

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> PingAnModelGatewaySettings:
        values = os.environ if environ is None else environ
        prefix = "SOC_PINGAN_MODEL_GATEWAY_"
        service_api_keys = _csv(values.get(prefix + "API_KEYS", ""))
        if not service_api_keys:
            raise ValueError(prefix + "API_KEYS is required")
        try:
            provider = PingAnModelProvider(values.get(prefix + "PROVIDER", PingAnModelProvider.EAGW.value).strip().lower())
        except ValueError as exc:
            raise ValueError(prefix + "PROVIDER must be 'eagw' or 'openai'") from exc
        base_url = _required(values, prefix + "UPSTREAM_BASE_URL")
        parsed_url = urlsplit(base_url)
        allowed_hosts = _csv(values.get(prefix + "ALLOWED_HOSTS", ""))
        if not allowed_hosts and parsed_url.hostname:
            allowed_hosts = (parsed_url.hostname,)
        route_kwargs: dict[str, Any] = {}
        if provider is PingAnModelProvider.OPENAI:
            route_kwargs["api_key"] = _required(
                values,
                prefix + "UPSTREAM_API_KEY",
            )
        else:
            route_kwargs.update(
                {
                    "app_key": _required(values, prefix + "APP_KEY"),
                    "app_secret": _required(values, prefix + "APP_SECRET"),
                    "scene_id": _required(values, prefix + "SCENE_ID"),
                    "openapi_code": values.get(
                        prefix + "OPENAPI_CODE",
                        "API035059",
                    ).strip(),
                    "openapi_credential": _required(
                        values,
                        prefix + "OPENAPI_CREDENTIAL",
                    ),
                    "rsa_private_key_hex": _private_key_hex(values, prefix=prefix),
                }
            )
        bind_host = values.get(prefix + "HOST", "127.0.0.1").strip()
        if bind_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("model gateway HOST must remain loopback-only")
        return cls(
            route=PingAnModelRoute(
                alias=values.get(
                    prefix + "MODEL_ALIAS",
                    "deepseek-v4-flash",
                ),
                upstream_model=values.get(
                    prefix + "UPSTREAM_MODEL",
                    "deepseek-v4-flash-0731",
                ),
                provider=provider,
                base_url=base_url,
                allowed_hosts=allowed_hosts,
                allow_insecure_http=_parse_bool(
                    values.get(prefix + "ALLOW_INSECURE_HTTP", "false"),
                    name=prefix + "ALLOW_INSECURE_HTTP",
                ),
                **route_kwargs,
            ),
            service_api_keys=service_api_keys,
            bind_host=bind_host,
            port=_parse_int(
                values.get(prefix + "PORT", "4001"),
                name=prefix + "PORT",
                minimum=1,
                maximum=65_535,
            ),
            max_request_bytes=_parse_int(
                values.get(prefix + "MAX_REQUEST_BYTES", "2000000"),
                name=prefix + "MAX_REQUEST_BYTES",
                minimum=1,
            ),
            max_concurrency=_parse_int(
                values.get(prefix + "MAX_CONCURRENCY", "1"),
                name=prefix + "MAX_CONCURRENCY",
                minimum=1,
            ),
            admission_timeout_seconds=_parse_float(
                values.get(prefix + "ADMISSION_TIMEOUT_SECONDS", "5"),
                name=prefix + "ADMISSION_TIMEOUT_SECONDS",
                minimum=0.001,
            ),
            upstream_timeout_seconds=_parse_float(
                values.get(prefix + "UPSTREAM_TIMEOUT_SECONDS", "600"),
                name=prefix + "UPSTREAM_TIMEOUT_SECONDS",
                minimum=0.001,
            ),
        )


class PingAnModelGateway:
    """Bounded async proxy for the single SOC model capacity pool."""

    def __init__(
        self,
        *,
        routes: Sequence[PingAnModelRoute],
        client: httpx.AsyncClient,
        max_concurrency: int = 1,
        admission_timeout_seconds: float = 5.0,
        upstream_timeout_seconds: float = 600.0,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        for name, value in (
            ("admission_timeout_seconds", admission_timeout_seconds),
            ("upstream_timeout_seconds", upstream_timeout_seconds),
        ):
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        route_map = {route.alias: route for route in routes}
        if not route_map:
            raise ValueError("at least one model route is required")
        if len(route_map) != len(routes):
            raise ValueError("model aliases must be unique")
        self._routes = route_map
        self._client = client
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_concurrency = max_concurrency
        self._admission_timeout_seconds = admission_timeout_seconds
        self._upstream_timeout_seconds = upstream_timeout_seconds
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._metrics_lock = Lock()
        self._in_flight = 0
        self._accepted_total = 0
        self._rejected_total = 0
        self._completed_total = 0
        self._failed_total = 0

    @property
    def model_aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self._routes))

    def model_inventory(self) -> list[dict[str, str]]:
        return [
            {
                "alias": route.alias,
                "upstream_model": route.upstream_model,
                "provider": route.provider.value,
            }
            for route in sorted(self._routes.values(), key=lambda item: item.alias)
        ]

    def capacity_snapshot(self) -> dict[str, int]:
        with self._metrics_lock:
            return {
                "max_concurrency": self._max_concurrency,
                "in_flight": self._in_flight,
                "accepted_total": self._accepted_total,
                "rejected_total": self._rejected_total,
                "completed_total": self._completed_total,
                "failed_total": self._failed_total,
            }

    async def complete(
        self,
        request_body: Mapping[str, Any],
    ) -> PingAnModelGatewayResponse:
        started = time.monotonic()
        payload, route, request_metadata = self._prepare_request(request_body)
        admission_started = time.monotonic()
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self._admission_timeout_seconds,
            )
        except TimeoutError as exc:
            with self._metrics_lock:
                self._rejected_total += 1
            raise PingAnModelGatewayBusyError("model capacity is busy; retry the durable processing job") from exc

        queue_wait_ms = round((time.monotonic() - admission_started) * 1000, 3)
        with self._metrics_lock:
            self._in_flight += 1
            self._accepted_total += 1
        provider_started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    urljoin(route.base_url + "/", "chat/completions"),
                    headers=self._upstream_headers(route),
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                ),
                timeout=self._upstream_timeout_seconds,
            )
            provider_duration_ms = round(
                (time.monotonic() - provider_started) * 1000,
                3,
            )
            body = self._validate_upstream_response(response, route=route)
        except (TimeoutError, httpx.TimeoutException) as exc:
            self._mark_failed()
            raise PingAnModelGatewayUpstreamTimeoutError("model upstream timed out") from exc
        except httpx.HTTPError as exc:
            self._mark_failed()
            raise PingAnModelGatewayUpstreamError(f"model upstream transport failed:{type(exc).__name__}") from exc
        except PingAnModelGatewayError:
            self._mark_failed()
            raise
        else:
            with self._metrics_lock:
                self._completed_total += 1
            usage_status = "reported" if isinstance(body.get("usage"), Mapping) else "unavailable"
            return PingAnModelGatewayResponse(
                status_code=200,
                body=body,
                metadata={
                    **request_metadata,
                    "public_model_alias": route.alias,
                    "upstream_model": route.upstream_model,
                    "provider": route.provider.value,
                    "usage_measurement_status": usage_status,
                    "admission_wait_duration_ms": queue_wait_ms,
                    "provider_duration_ms": provider_duration_ms,
                    "gateway_total_duration_ms": round(
                        (time.monotonic() - started) * 1000,
                        3,
                    ),
                },
            )
        finally:
            with self._metrics_lock:
                self._in_flight -= 1
            self._semaphore.release()

    def _prepare_request(
        self,
        request_body: Mapping[str, Any],
    ) -> tuple[dict[str, Any], PingAnModelRoute, dict[str, Any]]:
        model = request_body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise PingAnModelGatewayRequestError("model is required")
        route = self._routes.get(model.strip())
        if route is None:
            raise PingAnModelGatewayRequestError("requested model alias is not configured")
        messages = request_body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise PingAnModelGatewayRequestError("messages must be a non-empty array")
        if request_body.get("stream") is True:
            raise PingAnModelGatewayStreamingNotEnabledError("streaming is not enabled for the SOC model gateway")

        payload = copy.deepcopy(dict(request_body))
        nested_extra = payload.pop("extra_body", None)
        if nested_extra is not None:
            if not isinstance(nested_extra, Mapping):
                raise PingAnModelGatewayRequestError("extra_body must be an object")
            for key, value in nested_extra.items():
                if key != "model":
                    payload.setdefault(str(key), value)
        template_kwargs = payload.get("chat_template_kwargs")
        if template_kwargs is not None and not isinstance(template_kwargs, Mapping):
            raise PingAnModelGatewayRequestError("chat_template_kwargs must be an object")
        template_kwargs = dict(template_kwargs or {})
        enable_thinking = template_kwargs.get("enable_thinking")
        if enable_thinking is not None and not isinstance(enable_thinking, bool):
            raise PingAnModelGatewayRequestError("chat_template_kwargs.enable_thinking must be boolean")
        reasoning_effort = template_kwargs.get("reasoning_effort")
        if reasoning_effort is not None and (not isinstance(reasoning_effort, str) or len(reasoning_effort) > 32):
            raise PingAnModelGatewayRequestError("chat_template_kwargs.reasoning_effort must be a short string")
        if route.provider is PingAnModelProvider.EAGW:
            payload.pop("model", None)
        else:
            payload["model"] = route.upstream_model
        return (
            payload,
            route,
            {
                "thinking_enabled_requested": enable_thinking is True,
                "reasoning_effort_requested": reasoning_effort,
            },
        )

    def _upstream_headers(self, route: PingAnModelRoute) -> dict[str, str]:
        if route.provider is PingAnModelProvider.OPENAI:
            return {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {route.api_key}",
                "X-Request-ID": f"soc-model-{uuid.uuid4().hex}",
            }
        request_time = str(self._now_ms())
        return {
            "Content-Type": "application/json",
            "openApiCode": str(route.openapi_code),
            "openApiCredential": str(route.openapi_credential),
            "openApiRequestTime": request_time,
            "openApiSignature": _rsa_signature(
                str(route.rsa_private_key_hex),
                request_time,
            ),
            "gpt_app_key": str(route.app_key),
            "gpt_signature": _gpt_signature(
                str(route.app_key),
                str(route.app_secret),
                request_time,
            ),
            "scene_id": str(route.scene_id),
            "request_id": f"soc-model-{uuid.uuid4().hex}",
        }

    @staticmethod
    def _validate_upstream_response(
        response: httpx.Response,
        *,
        route: PingAnModelRoute,
    ) -> dict[str, Any]:
        if response.status_code >= 400:
            raise PingAnModelGatewayUpstreamError(f"model upstream returned HTTP {response.status_code}")
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise PingAnModelGatewayUpstreamError("model upstream returned malformed JSON") from exc
        if not isinstance(body, dict):
            raise PingAnModelGatewayUpstreamError("model upstream returned a non-object response")
        if route.provider is PingAnModelProvider.EAGW and (body.get("success") is False or body.get("resultCode") not in (None, "", 0, "0")):
            raise PingAnModelGatewayUpstreamError("model upstream returned a business error")
        if not isinstance(body.get("choices"), list):
            raise PingAnModelGatewayUpstreamError("model upstream response does not contain choices")
        return body

    def _mark_failed(self) -> None:
        with self._metrics_lock:
            self._failed_total += 1


def _gpt_signature(app_key: str, app_secret: str, request_time: str) -> str:
    params = {
        "openApiRequestTime": request_time,
        "appKey": app_key,
        "appSecret": app_secret,
    }
    query = urlencode(params).lower().encode("utf-8")
    digest = hmac.new(app_secret.encode("utf-8"), query, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _rsa_signature(private_key_hex: str, request_time: str) -> str:
    private_key = _load_rsa_private_key(private_key_hex)
    return (
        private_key.sign(
            request_time.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        .hex()
        .upper()
    )


def _load_rsa_private_key(private_key_hex: str) -> rsa.RSAPrivateKey:
    try:
        key_bytes = binascii.unhexlify(private_key_hex.strip())
        key = serialization.load_der_private_key(key_bytes, password=None)
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("invalid EAGW RSA private key configuration") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("EAGW private key must be RSA")
    return key


def _private_key_hex(values: Mapping[str, str], *, prefix: str) -> str:
    inline = values.get(prefix + "RSA_PRIVATE_KEY_HEX", "").strip()
    file_name = values.get(prefix + "RSA_PRIVATE_KEY_FILE", "").strip()
    if inline and file_name:
        raise ValueError("configure only one of RSA_PRIVATE_KEY_HEX or RSA_PRIVATE_KEY_FILE")
    if inline:
        _load_rsa_private_key(inline)
        return inline
    if not file_name:
        raise ValueError(prefix + "RSA_PRIVATE_KEY_FILE or " + prefix + "RSA_PRIVATE_KEY_HEX is required")
    path = Path(file_name).expanduser()
    if not path.is_file():
        raise ValueError("EAGW RSA private key file does not exist")
    raw = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except ValueError:
        try:
            key = serialization.load_der_private_key(raw, password=None)
        except ValueError as exc:
            raise ValueError("EAGW RSA private key file is invalid") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("EAGW private key file must contain an RSA key")
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(name + " is required")
    return value


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(name + " must be a boolean value")


def _parse_int(
    value: str,
    *,
    name: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(name + " must be an integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise ValueError(name + " is outside the supported range")
    return parsed


def _parse_float(value: str, *, name: str, minimum: float) -> float:
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ValueError(name + " must be a number") from exc
    if not isfinite(parsed) or parsed < minimum:
        raise ValueError(name + " is outside the supported range")
    return parsed


__all__ = [
    "PingAnModelGateway",
    "PingAnModelGatewayBusyError",
    "PingAnModelGatewayError",
    "PingAnModelGatewayRequestError",
    "PingAnModelGatewayResponse",
    "PingAnModelGatewaySettings",
    "PingAnModelGatewayStreamingNotEnabledError",
    "PingAnModelGatewayUpstreamError",
    "PingAnModelGatewayUpstreamTimeoutError",
    "PingAnModelProvider",
    "PingAnModelRoute",
]
