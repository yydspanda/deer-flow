#!/usr/bin/env python3
"""Validate long encoded-span removal inside ``zeusRawLogs`` LLM input.

This script never decodes values and never mutates the source object. It builds a
separate JSON-compatible projection where only long, encoding-shaped spans below
``zeusRawLogs`` are replaced by compact markers. The source path and a digest
remain available in a sidecar report for audit and tuning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypeAlias

JsonValue: TypeAlias = (
    dict[str, "JsonValue"] | list["JsonValue"] | str | int | float | bool | None
)

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
    """One span removed from the LLM-only projection."""

    path: str
    kind: str
    original_chars: int
    sha256: str


def compact_encoded_spans(
    value: JsonValue,
    *,
    min_blob_chars: int = 256,
) -> tuple[JsonValue, list[OmittedEncodedSpan]]:
    """Return an LLM-only copy with long encoded-looking spans replaced.

    Detection is deliberately content-only: no topic name or field-name policy is
    used. Nothing is decoded. Short hashes, IDs, and ordinary values remain intact.
    """

    if min_blob_chars < 128:
        raise ValueError("min_blob_chars must be at least 128")

    data_uri_re = re.compile(
        rf"data:[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+(?:;[^,\s]{{1,80}})*;base64,"
        rf"[A-Za-z0-9+/_=-]{{{min_blob_chars},}}",
        re.IGNORECASE,
    )
    hex_re = re.compile(
        rf"(?<![0-9A-Fa-f])[0-9A-Fa-f]{{{min_blob_chars},}}(?![0-9A-Fa-f])"
    )
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
            return {
                key: visit(child, _child_path(path, key)) for key, child in item.items()
            }
        if isinstance(item, list):
            return [
                visit(child, f"{path}[{index}]") for index, child in enumerate(item)
            ]
        if not isinstance(item, str):
            return item

        compacted = item
        for kind, pattern, min_unique_chars in patterns:
            compacted = pattern.sub(
                lambda match, kind=kind, min_unique_chars=min_unique_chars: (
                    _replacement(
                        match.group(0),
                        path=path,
                        kind=kind,
                        min_unique_chars=min_unique_chars,
                        omissions=omissions,
                    )
                ),
                compacted,
            )
        return compacted

    return visit(value, "$"), omissions


def compact_zeus_raw_logs(
    value: JsonValue,
    *,
    min_blob_chars: int = 256,
) -> tuple[JsonValue, list[OmittedEncodedSpan]]:
    """Compact encoded spans only inside values named ``zeusRawLogs``.

    The function recursively locates ``zeusRawLogs`` containers but does not
    inspect or change string values anywhere else in the input.
    """

    omissions: list[OmittedEncodedSpan] = []

    def visit(item: JsonValue, path: str) -> JsonValue:
        if isinstance(item, dict):
            result: dict[str, JsonValue] = {}
            for key, child in item.items():
                child_path = _child_path(path, key)
                if key == "zeusRawLogs":
                    compacted, local_omissions = compact_encoded_spans(
                        child,
                        min_blob_chars=min_blob_chars,
                    )
                    result[key] = compacted
                    omissions.extend(
                        OmittedEncodedSpan(
                            path=f"{child_path}{omission.path.removeprefix('$')}",
                            kind=omission.kind,
                            original_chars=omission.original_chars,
                            sha256=omission.sha256,
                        )
                        for omission in local_omissions
                    )
                else:
                    result[key] = visit(child, child_path)
            return result
        if isinstance(item, list):
            return [
                visit(child, f"{path}[{index}]") for index, child in enumerate(item)
            ]
        return item

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
    return f"<ENCODED:{kind}:{len(candidate)}:OMITTED>"


def _child_path(path: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


def _compact_json_size(value: JsonValue) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _run_self_check() -> None:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    long_blob = alphabet * 5
    jwt_like = f"{'a' * 24}.{'bC3_' * 40}"
    source: JsonValue = {
        "hash": "8b046b92886dfc7418569b7b9f8e6328",
        "business_id": "QbJK/jZFu",
        "mixed": f"prefix={long_blob};suffix=kept",
        "token": jwt_like,
    }
    compacted, omissions = compact_encoded_spans(source)

    assert source["mixed"] == f"prefix={long_blob};suffix=kept"
    assert isinstance(compacted, dict)
    assert compacted["hash"] == source["hash"]
    assert compacted["business_id"] == source["business_id"]
    assert str(compacted["mixed"]).startswith("prefix=<ENCODED:base64_like:")
    assert str(compacted["mixed"]).endswith(";suffix=kept")
    assert str(compacted["token"]).startswith("<ENCODED:jwt_like:")
    assert {item.kind for item in omissions} == {"base64_like", "jwt_like"}

    scoped_source: JsonValue = {
        "outside": long_blob,
        "alert": {
            "hitLog": [
                {
                    "zeusRawLogs": [
                        {
                            "message": f"prefix={long_blob};suffix=kept",
                        }
                    ]
                }
            ]
        },
    }
    scoped_compacted, scoped_omissions = compact_zeus_raw_logs(scoped_source)
    assert isinstance(scoped_compacted, dict)
    assert scoped_compacted["outside"] == long_blob
    assert len(scoped_omissions) == 1
    assert scoped_omissions[0].path == "$.alert.hitLog[0].zeusRawLogs[0].message"
    assert "<ENCODED:base64_like:" in str(scoped_compacted["alert"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=Path("datas/apt-1965449.json"),
        help="JSON payload to validate (default: datas/apt-1965449.json)",
    )
    parser.add_argument(
        "--min-blob-chars",
        type=int,
        default=256,
        help="Minimum generic Base64/Hex span length (default: 256)",
    )
    parser.add_argument(
        "--output", type=Path, help="Optional path for the compacted LLM-only JSON"
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Run deterministic assertions before the sample",
    )
    args = parser.parse_args()

    if args.self_check:
        _run_self_check()
        print("self_check=passed")

    source = json.loads(args.input.read_text(encoding="utf-8"))
    compacted, omissions = compact_zeus_raw_logs(
        source, min_blob_chars=args.min_blob_chars
    )
    before_chars = _compact_json_size(source)
    after_chars = _compact_json_size(compacted)
    saved_chars = before_chars - after_chars

    print(f"input={args.input}")
    print(f"min_blob_chars={args.min_blob_chars}")
    print(f"omitted_spans={len(omissions)}")
    print(f"before_chars={before_chars}")
    print(f"after_chars={after_chars}")
    print(f"saved_chars={saved_chars}")
    print(
        f"reduction_percent={(saved_chars / before_chars * 100) if before_chars else 0:.2f}"
    )
    for index, omission in enumerate(omissions, start=1):
        print(
            f"match[{index}] kind={omission.kind} chars={omission.original_chars} "
            f"sha256={omission.sha256[:12]} path={omission.path}"
        )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "source": str(args.input),
                    "policy": {
                        "mode": "llm_projection_only",
                        "scope": "zeusRawLogs",
                        "decoding": False,
                        "min_blob_chars": args.min_blob_chars,
                    },
                    "stats": {
                        "omitted_spans": len(omissions),
                        "before_chars": before_chars,
                        "after_chars": after_chars,
                        "saved_chars": saved_chars,
                        "reduction_percent": round(
                            (saved_chars / before_chars * 100) if before_chars else 0, 2
                        ),
                    },
                    "omissions": [asdict(item) for item in omissions],
                    "llm_projection": compacted,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
