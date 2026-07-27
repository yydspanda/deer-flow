"""Content-based encoded-span compaction for model-bound evidence projections."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

type JsonValue = dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None

_PEM_RE = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9][A-Z0-9 -]{0,62})-----.*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)
_JWT_LIKE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9_-]{16,}\.){1,4}[A-Za-z0-9_-]{16,}"
    r"(?![A-Za-z0-9_-])"
)
_PERCENT_RE = re.compile(r"(?<!%)(?:%[0-9A-Fa-f]{2}){32,}")
_HEX_ESCAPE_RE = re.compile(r"(?<!\\x)(?:\\x[0-9A-Fa-f]{2}){32,}")
_UNICODE_ESCAPE_RE = re.compile(r"(?<!\\u)(?:\\u[0-9A-Fa-f]{4}){24,}")


@dataclass(frozen=True)
class OmittedEncodedSpan:
    """One encoded-looking span removed from a model-only projection."""

    path: str
    kind: str
    original_chars: int
    sha256: str


def compact_encoded_spans(
    value: JsonValue,
    *,
    min_blob_chars: int = 256,
) -> tuple[JsonValue, list[OmittedEncodedSpan]]:
    """Replace long encoding-shaped spans without decoding or mutating input."""

    if min_blob_chars < 128:
        raise ValueError("min_blob_chars must be at least 128")

    data_uri_re = re.compile(
        rf"data:[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+(?:;[^,\s]{{1,80}})*;base64,"
        rf"[A-Za-z0-9+/_=-]{{{min_blob_chars},}}",
        re.IGNORECASE,
    )
    hex_re = re.compile(rf"(?<![0-9A-Fa-f])[0-9A-Fa-f]{{{min_blob_chars},}}(?![0-9A-Fa-f])")
    base64_re = re.compile(
        rf"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{{{min_blob_chars},}}={{0,2}}"
        rf"(?![A-Za-z0-9+/_-])"
    )
    patterns = (
        ("pem", _PEM_RE, 0),
        ("data_uri_base64", data_uri_re, 0),
        ("jwt_like", _JWT_LIKE_RE, 0),
        ("percent_encoded", _PERCENT_RE, 0),
        ("hex_escape", _HEX_ESCAPE_RE, 0),
        ("unicode_escape", _UNICODE_ESCAPE_RE, 0),
        ("hex_like", hex_re, 8),
        ("base64_like", base64_re, 16),
    )
    omissions: list[OmittedEncodedSpan] = []

    def visit(item: JsonValue, path: str) -> JsonValue:
        if isinstance(item, dict):
            return {key: visit(child, _child_path(path, key)) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child, f"{path}[{index}]") for index, child in enumerate(item)]
        if not isinstance(item, str):
            return item

        compacted = item
        for kind, pattern, min_unique_chars in patterns:
            compacted = pattern.sub(
                lambda match, kind=kind, min_unique_chars=min_unique_chars: _replacement(
                    match.group(0),
                    path=path,
                    kind=kind,
                    min_unique_chars=min_unique_chars,
                    omissions=omissions,
                ),
                compacted,
            )
        return compacted

    return visit(value, "$"), omissions


def _replacement(
    candidate: str,
    *,
    path: str,
    kind: str,
    min_unique_chars: int,
    omissions: list[OmittedEncodedSpan],
) -> str:
    normalized = candidate.rstrip("=")
    if min_unique_chars and len(set(normalized)) < min_unique_chars:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    omissions.append(
        OmittedEncodedSpan(
            path=path,
            kind=kind,
            original_chars=len(candidate),
            sha256=digest,
        )
    )
    return f"<ENCODED:{kind}:{len(candidate)}:sha256={digest[:12]}:OMITTED>"


def _child_path(path: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


__all__ = [
    "JsonValue",
    "OmittedEncodedSpan",
    "compact_encoded_spans",
]
