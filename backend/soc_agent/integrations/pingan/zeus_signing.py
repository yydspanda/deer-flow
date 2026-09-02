"""Self-contained signer for the reviewed PingAn ZEUS gateway contract."""

from __future__ import annotations

import hashlib
import json
import random
import time
from typing import Any


def isec_sign(
    data: Any = None,
    app_id: str | None = None,
    app_key: str | None = None,
) -> dict[str, str]:
    """Build the legacy ZEUS request headers without importing the old app."""

    normalized_app_id = (app_id or "").strip()
    if not normalized_app_id or not app_key:
        raise ValueError("app_id and app_key are required")
    return _build_isec_headers(
        data=data,
        app_id=normalized_app_id,
        app_key=app_key,
        timestamp=str(int(time.time() * 1000)),
        nonce=str(random.randint(0, 9_999_999)),
    )


def _build_isec_headers(
    *,
    data: Any,
    app_id: str,
    app_key: str,
    timestamp: str,
    nonce: str,
) -> dict[str, str]:
    request_body = serialize_isec_json_body(data).decode("utf-8")
    sign_module = "@@".join((app_id, timestamp, nonce, "SHA256", request_body, app_key))
    return {
        "x-sec-route-env": "gray",
        "App-Sign": hashlib.sha256(sign_module.encode("UTF-8")).hexdigest(),
        "App-Id": app_id,
        "App-Timestamp": timestamp,
        "App-Nonce": nonce,
        "App-Signature-Method": "SHA256",
        "APP-key": app_key,
        "companyCode": "ada82427b0de4a1ab890f5ed1a557c14",
    }


def serialize_isec_json_body(data: Any) -> bytes:
    """Serialize the exact legacy JSON bytes covered by ``App-Sign``."""

    if data is None:
        return b""
    return json.dumps(data).encode("utf-8")


__all__ = ["isec_sign", "serialize_isec_json_body"]
