"""Bounded JSON member normalization; never resolve contradictory model output."""

import hashlib
import json
import math
import re

CONTRACT = "topic-json-unique-keys-v1"
MAX_BYTES = 64 * 1024
MAX_DEPTH = 64
MAX_NODES = 20000
MAX_LOCATIONS = 16
KNOWN_KEYS = frozenset(
    "status filter sample_alert_ids issue recommendation certainty review_notes "
    "sample_request topic_review topic expression name operator value decision "
    "field_roles role basis other_detection_impact field text".split()
)


class TopicJSONError(ValueError):
    def __init__(self, code, diagnostics=None):
        self.code = code
        self.diagnostics = diagnostics
        super().__init__(code)


class _Pairs(list):
    """Keep all object members distinct from ordinary JSON arrays."""


class _Number:
    def __init__(self, literal):
        # Keep the literal for equality and re-encoding: Python float rounding
        # and bool/int equality must never hide contradictory values.
        self.literal = literal
        if any(c in literal for c in ".eE"):
            if not math.isfinite(float(literal)):
                raise TopicJSONError("nonfinite_json_value")
        else:
            int(literal)


def _invalid_constant(_):
    raise TopicJSONError("nonfinite_json_value")


def _same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, _Number):
        return left.literal == right.literal
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _encode(value):
    if isinstance(value, _Number):
        return value.literal
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=True) + ":" + _encode(item)
                for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _key_label(key):
    if key in KNOWN_KEYS or re.fullmatch(r"[A-Z]{1,2}", key):
        return key
    return (
        "unknown-key-sha256:"
        + hashlib.sha256(key.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
    )


def normalize(text):
    """Return JSON text with identical duplicates collapsed and small diagnostics.

    Preserve the surrounding fence/tail verbatim for the original strict parser
    to validate, including its incomplete-fence warning. No salvage of prose,
    truncated JSON, second documents, differing types, or differing values.
    """
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise TopicJSONError("topic_reply_too_large")
    stripped = text.strip()
    start = 0
    if stripped.startswith(("```", "~~~")):
        opening = re.match(
            r"(?P<fence>`{3,}|~{3,})[ \t]*(?:json(?=[\s{\[]|$))?\s*",
            stripped,
            re.IGNORECASE,
        )
        if opening is None:
            raise TopicJSONError("invalid_json_fence")
        start = opening.end()
    decoder = json.JSONDecoder(
        object_pairs_hook=_Pairs,
        parse_constant=_invalid_constant,
        parse_int=_Number,
        parse_float=_Number,
    )
    try:
        value, end = decoder.raw_decode(stripped, start)
    except TopicJSONError:
        raise
    except (ValueError, RecursionError, OverflowError):
        raise TopicJSONError("invalid_json") from None
    diagnostics = {
        "contract_version": CONTRACT,
        "identical_duplicates_collapsed": 0,
        "conflicting_duplicates": 0,
        "duplicate_locations": [],
        "locations_truncated": False,
    }
    nodes = 0

    def visit(item, path, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise TopicJSONError("topic_json_structure_limit")
        if isinstance(item, _Pairs):
            output = {}
            for key, child in item:
                child_path = path + [_key_label(key)]
                converted = visit(child, child_path, depth + 1)
                if key not in output:
                    output[key] = converted
                    continue
                identical = _same(output[key], converted)
                counter = (
                    "identical_duplicates_collapsed"
                    if identical
                    else "conflicting_duplicates"
                )
                diagnostics[counter] += 1
                if len(diagnostics["duplicate_locations"]) < MAX_LOCATIONS:
                    diagnostics["duplicate_locations"].append(
                        {
                            "path": child_path[:12],
                            "path_truncated": len(child_path) > 12,
                            "comparison": "identical" if identical else "conflict",
                        }
                    )
                else:
                    diagnostics["locations_truncated"] = True
                # A conflict is recorded, never resolved by overwriting either
                # member. The entire document will be rejected below.
            return output
        if isinstance(item, list):
            return [
                visit(child, path + [index], depth + 1)
                for index, child in enumerate(item)
            ]
        return item

    document = visit(value, [], 0)
    if diagnostics["conflicting_duplicates"]:
        raise TopicJSONError("duplicate_json_key", diagnostics)
    normalized = text
    if diagnostics["identical_duplicates_collapsed"]:
        normalized = stripped[:start] + _encode(document) + stripped[end:]
    return normalized, diagnostics
