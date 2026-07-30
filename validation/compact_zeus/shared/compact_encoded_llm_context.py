#!/usr/bin/env python3
"""Validate long encoded-span removal inside ``zeusRawLogs`` LLM input.

This script never decodes values and never mutates the source object. It builds a
separate JSON-compatible projection where only long, encoding-shaped spans below
``zeusRawLogs`` are replaced by compact markers. The source path and a digest
remain available in a sidecar report for audit and tuning.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from soc_agent.pipeline.encoded_context import (  # noqa: E402
    JsonValue,
    OmittedEncodedSpan,
    compact_encoded_spans,
)


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
        default=Path("datas/legacy_demos/apt-1965449.json"),
        help=(
            "JSON payload to validate (default: datas/legacy_demos/apt-1965449.json)"
        ),
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
            f"match[{index}] kind={omission.kind} chars={omission.original_chars} sha256={omission.sha256[:12]} path={omission.path}"
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
