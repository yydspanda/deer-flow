"""Deterministic parsers for raw messages carried by the PingAn alert envelope."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from json_repair import loads as repair_json_loads

from soc_agent.contracts import MessageFieldSpan, MessageSyntaxCoverage, MessageTextSpan, NestedJsonRepairObservation, NestedJsonRepairStatus, ParsedRawMessageEvidence

_QUOTED_KV_START_RE = re.compile(r'(?<![^\s,])([A-Za-z_][A-Za-z0-9_.]*)="')
_PROGRAM_PREFIX_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*(?:\[\d+\])?:?\s+")
_COMMA_KV_START_RE = re.compile(r"(?:^|,)([A-Za-z_][A-Za-z0-9_]*)=")
_SYSLOG_HEADER_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T[^ ]+)\s+"
    r"(?P<hostname>\S+)\s+(?P<program>[^\[]+)\[(?P<pid>\d+)\]"
)
_NESTED_JSON_FIELD_NAMES = frozenset(
    {
        "rule_labels",
        "req_body",
        "rsp_body",
        "request_header_str",
        "response_header_str",
        "response_hqeader_str",
    }
)
_HTTP_HEADER_FIELD_NAMES = frozenset({"req_header", "rsp_header"})
_FORWARDED_FIELD_NAMES = frozenset({"xff", "x_forwarded_for"})
_MAX_NESTED_JSON_CHARS = 64_000
_MAX_JSON_PREFIX_CHARS = 512


@dataclass(frozen=True)
class _RepairPolicy:
    allowed_root_types: tuple[type, ...]
    max_depth: int = 8
    max_nodes: int = 512
    max_key_chars: int = 128


_REPAIR_POLICIES = {
    "req_body": _RepairPolicy((dict, list)),
    "rsp_body": _RepairPolicy((dict, list)),
    "rule_labels": _RepairPolicy((dict,), max_depth=6, max_nodes=256),
    "request_header_str": _RepairPolicy((dict,), max_depth=4, max_nodes=256),
    "response_header_str": _RepairPolicy((dict,), max_depth=4, max_nodes=256),
    "response_hqeader_str": _RepairPolicy((dict,), max_depth=4, max_nodes=256),
}


class PingAnRawMessageParser(Protocol):
    """Parser contract used by the PingAn source adapter registry."""

    parser_name: str
    parser_version: str

    def parse(self, message: str, *, source_path: str) -> ParsedRawMessageEvidence | None: ...


class PingAnDelimitedJsonMessageParser:
    parser_name = "pingan_delimited_json"
    parser_version = "v2"

    def parse(self, message: str, *, source_path: str) -> ParsedRawMessageEvidence | None:
        if "|!" not in message:
            return None
        prefix, candidate = message.rsplit("|!", 1)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return _result(
            message,
            source_path=source_path,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            fields=parsed,
            header={"prefix": prefix},
        )


class PingAnJsonObjectMessageParser:
    """Parse one complete JSON object with an optional bounded text prefix."""

    parser_name = "pingan_json_object"
    parser_version = "v1"

    def parse(self, message: str, *, source_path: str) -> ParsedRawMessageEvidence | None:
        search_window = message[: _MAX_JSON_PREFIX_CHARS + 1]
        decoder = json.JSONDecoder()
        for match in re.finditer(r"{", search_window):
            start = match.start()
            try:
                parsed, end = decoder.raw_decode(message[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict) or message[start + end :].strip():
                continue
            prefix = message[:start].strip()
            return _result(
                message,
                source_path=source_path,
                parser_name=self.parser_name,
                parser_version=self.parser_version,
                fields=parsed,
                header={"prefix": prefix} if prefix else {},
            )
        return None


class PingAnQuotedKvMessageParser:
    parser_name = "pingan_quoted_kv"
    parser_version = "v4"

    def parse(self, message: str, *, source_path: str) -> ParsedRawMessageEvidence | None:
        body, _ = _strip_bracketed_prefix(message)
        comma_start = _COMMA_KV_START_RE.match(body)
        if comma_start and body[comma_start.end() : comma_start.end() + 1] != '"' and len(_COMMA_KV_START_RE.findall(body)) >= 2:
            return None
        starts = list(_QUOTED_KV_START_RE.finditer(message))
        if len(starts) < 2:
            return None
        header_match = _SYSLOG_HEADER_RE.match(message)
        header = header_match.groupdict() if header_match is not None else {}
        prefix_end = header_match.end() if header_match is not None else 0
        if not prefix_end:
            program = _PROGRAM_PREFIX_RE.fullmatch(message[: starts[0].start()])
            if program is not None:
                prefix_end = program.end()
                header = {"prefix": program.group().strip()}
        fields, coverage = _scan_quoted_kv(message, starts, prefix_end=prefix_end)
        if len(coverage.field_spans) < 2:
            return None
        warnings = []
        if coverage.unconsumed_spans:
            warnings.append(f"quoted KV unconsumed spans: {len(coverage.unconsumed_spans)}")
        if coverage.conflicting_fields:
            warnings.append("quoted KV conflicting duplicate fields: " + ", ".join(coverage.conflicting_fields))
        return _result(
            message,
            source_path=source_path,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            fields=fields,
            header=header,
            syntax_coverage=coverage,
            parser_warnings=warnings,
        )


def _next_unescaped_quote(message: str, start: int, stop: int) -> int | None:
    while start < stop:
        if message[start] == "\\":
            start += 2
        elif message[start] == '"':
            return start
        else:
            start += 1
    return None


def _kv_boundary(message: str, position: int) -> bool:
    return position == len(message) or message[position].isspace() or message[position] == ","


def _nested_quoted_value_end(message: str, start: int, stop: int) -> int | None:
    # One outer close after balanced literal quote pairs, before the next field.
    count, last = 0, None
    position = start
    while (quote := _next_unescaped_quote(message, position, stop)) is not None:
        count += 1
        last = quote
        position = quote + 1
    if count < 3 or count % 2 != 1 or last is None:
        return None
    if any(not _kv_boundary(message, pos) for pos in range(last + 1, stop)):
        return None
    return last


def _scan_quoted_kv(message: str, starts: list[re.Match[str]], *, prefix_end: int) -> tuple[dict[str, str], MessageSyntaxCoverage]:
    fields: dict[str, str] = {}
    occurrences: dict[str, set[str]] = {}
    duplicates: set[str] = set()
    recovered: set[str] = set()
    spans: list[MessageFieldSpan] = []
    consumed_end = prefix_end
    for index, match in enumerate(starts):
        if match.start() < consumed_end:
            continue
        # Never recover across a possible next field. Ambiguous text stays in raw.
        limit = starts[index + 1].start() if index + 1 < len(starts) else len(message)
        value_start = match.end()
        value_end = _next_unescaped_quote(message, value_start, limit)
        if value_end is None:
            continue
        was_recovered = False
        if value_end == value_start and not _kv_boundary(message, value_end + 1):
            outer_end = _nested_quoted_value_end(message, value_start, limit)
            if outer_end is None:
                continue
            value_end = outer_end
            was_recovered = True
        if not _kv_boundary(message, value_end + 1):
            continue
        name = match.group(1)
        value = html.unescape(message[value_start:value_end])
        if name in occurrences:
            duplicates.add(name)
        occurrences.setdefault(name, set()).add(value)
        fields[name] = value
        if was_recovered:
            recovered.add(name)
        consumed_end = value_end + 1
        spans.append(MessageFieldSpan(start=match.start(), end=consumed_end, field_name=name, value_start=value_start, value_end=value_end))

    conflicts = sorted(name for name, values in occurrences.items() if len(values) > 1)
    for name in conflicts:
        del fields[name]
    residuals = []
    cursor = prefix_end
    for start, end in [(span.start, span.end) for span in spans] + [(len(message), len(message))]:
        left, right = cursor, start
        while left < right and _kv_boundary(message, left):
            left += 1
        while right > left and _kv_boundary(message, right - 1):
            right -= 1
        if left < right:
            residuals.append(MessageTextSpan(start=left, end=right))
        cursor = end
    return fields, MessageSyntaxCoverage(
        complete=not residuals and not conflicts,
        field_spans=spans,
        prefix_span=MessageTextSpan(start=0, end=prefix_end) if prefix_end else None,
        unconsumed_spans=residuals,
        duplicate_fields=sorted(duplicates),
        conflicting_fields=conflicts,
        recovered_fields=sorted(recovered),
    )


class PingAnCommaKvMessageParser:
    parser_name = "pingan_comma_kv"
    parser_version = "v2"

    def parse(self, message: str, *, source_path: str) -> ParsedRawMessageEvidence | None:
        body, header = _strip_bracketed_prefix(message)
        matches = list(_COMMA_KV_START_RE.finditer(body))
        if len(matches) < 2:
            return None
        fields: dict[str, str] = {}
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            fields[match.group(1)] = body[start:end].rstrip(",")
        return _result(
            message,
            source_path=source_path,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            fields=fields,
            header=header,
        )


class PingAnLooseKvMessageParser:
    parser_name = "pingan_loose_kv"
    parser_version = "v2"

    def parse(self, message: str, *, source_path: str) -> ParsedRawMessageEvidence | None:
        try:
            tokens = shlex.split(message)
        except ValueError:
            return None
        fields: dict[str, str] = {}
        for token in tokens:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                fields[key] = value
        if len(fields) < 2:
            return None
        return _result(
            message,
            source_path=source_path,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            fields=fields,
            header={},
        )


DEFAULT_PINGAN_MESSAGE_PARSERS: tuple[PingAnRawMessageParser, ...] = (
    PingAnDelimitedJsonMessageParser(),
    PingAnJsonObjectMessageParser(),
    PingAnQuotedKvMessageParser(),
    PingAnCommaKvMessageParser(),
    PingAnLooseKvMessageParser(),
)


def parse_pingan_raw_message(
    message: str,
    *,
    source_path: str,
    parsers: Sequence[PingAnRawMessageParser] = DEFAULT_PINGAN_MESSAGE_PARSERS,
) -> ParsedRawMessageEvidence | None:
    """Return the first deterministic parse result for a PingAn raw message."""

    if not message.strip():
        return None
    for parser in parsers:
        result = parser.parse(message, source_path=source_path)
        if result is not None:
            return result
    return None


def _result(
    message: str,
    *,
    source_path: str,
    parser_name: str,
    parser_version: str,
    fields: Mapping[str, object],
    header: Mapping[str, object],
    syntax_coverage: MessageSyntaxCoverage | None = None,
    parser_warnings: Sequence[str] = (),
) -> ParsedRawMessageEvidence:
    normalized_fields = dict(fields)
    decoded_fields, repaired_fields, repair_observations, warnings = _decode_nested_fields(normalized_fields)
    return ParsedRawMessageEvidence(
        source_path=source_path,
        parser_name=parser_name,
        parser_version=parser_version,
        message_hash=hashlib.sha256(message.encode("utf-8")).hexdigest(),
        original_length=len(message),
        fields=normalized_fields,
        decoded_fields=decoded_fields,
        repaired_fields=repaired_fields,
        repair_observations=repair_observations,
        syntax_coverage=syntax_coverage,
        header=dict(header),
        warnings=[*parser_warnings, *warnings],
    )


def _decode_nested_fields(
    fields: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object], list[NestedJsonRepairObservation], list[str]]:
    """Decode explicitly supported nested payloads without flattening vendor fields."""

    decoded: dict[str, object] = {}
    repaired: dict[str, object] = {}
    repair_observations: list[NestedJsonRepairObservation] = []
    warnings: list[str] = []

    def visit(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                visit(item, (*path, str(key)))
            return
        if not path or not isinstance(value, str) or not value.strip():
            return

        field_name = path[-1].lower()
        if field_name in _NESTED_JSON_FIELD_NAMES:
            nested, repaired_value, repair_observation = _decode_json_string(
                value,
                path=".".join(path),
                warnings=warnings,
            )
            if nested is not None:
                _set_nested(decoded, path, nested)
            elif repaired_value is not None:
                _set_nested(repaired, path, repaired_value)
            if repair_observation is not None:
                repair_observations.append(repair_observation)
        elif field_name in _HTTP_HEADER_FIELD_NAMES:
            parsed_header = _decode_http_header(value)
            if parsed_header:
                _set_nested(decoded, path, parsed_header)
        elif field_name in _FORWARDED_FIELD_NAMES:
            chain = _forwarded_chain(value)
            if chain:
                _set_nested(decoded, path, chain)

    visit(fields, ())
    return decoded, repaired, repair_observations, warnings


def _decode_json_string(
    value: str,
    *,
    path: str,
    warnings: list[str],
) -> tuple[object | None, object | None, NestedJsonRepairObservation | None]:
    candidate = value.strip()
    if not candidate.startswith(("{", "[")):
        return None, None, None
    if len(candidate) > _MAX_NESTED_JSON_CHARS:
        warnings.append(f"nested JSON skipped because it exceeds size limit: {path}")
        return None, None, None
    try:
        return json.loads(candidate), None, None
    except json.JSONDecodeError:
        warnings.append(f"nested JSON decode failed: {path}")
    repaired, observation = _repair_json_projection(candidate, path=path)
    warnings.append(f"nested JSON repair {observation.status.value}: {path}")
    return None, repaired, observation


def _repair_json_projection(
    candidate: str,
    *,
    path: str,
) -> tuple[object | None, NestedJsonRepairObservation]:
    try:
        repaired, repair_log = repair_json_loads(
            candidate,
            skip_json_loads=True,
            logging=True,
        )
    except Exception as exc:  # noqa: BLE001 - parser boundary records typed repair failure
        return None, NestedJsonRepairObservation(
            field_path=path,
            status=NestedJsonRepairStatus.ERROR,
            reason=f"json_repair raised {type(exc).__name__}",
        )

    log_count = len(repair_log) if isinstance(repair_log, list) else 0
    rejection_reason = _repair_rejection_reason(candidate, repaired, field_name=path.rsplit(".", 1)[-1].lower())
    if rejection_reason is not None:
        return None, NestedJsonRepairObservation(
            field_path=path,
            status=NestedJsonRepairStatus.REJECTED,
            repair_log_count=log_count,
            reason=rejection_reason,
        )
    return repaired, NestedJsonRepairObservation(
        field_path=path,
        status=NestedJsonRepairStatus.ACCEPTED,
        repair_log_count=log_count,
        reason="repair preserved container shape and source-evidenced keys",
    )


def _repair_rejection_reason(candidate: str, repaired: Any, *, field_name: str) -> str | None:
    if candidate.startswith("{") and not isinstance(repaired, Mapping):
        return "repair changed object into a different root type"
    if candidate.startswith("[") and not isinstance(repaired, list):
        return "repair changed array into a different root type"
    if not isinstance(repaired, (Mapping, list)):
        return "repair did not produce a JSON object or array"
    if not repaired:
        return "repair produced an empty container"

    policy = _REPAIR_POLICIES.get(field_name, _RepairPolicy((dict, list)))
    if not isinstance(repaired, policy.allowed_root_types):
        return f"repair root type is not allowed for {field_name}"
    depth, node_count = _shape_metrics(repaired)
    if depth > policy.max_depth:
        return f"repair exceeds maximum depth for {field_name}"
    if node_count > policy.max_nodes:
        return f"repair exceeds maximum node count for {field_name}"
    if any(not key or len(key) > policy.max_key_chars or any(ord(char) < 32 for char in key) for key in _mapping_keys(repaired)):
        return f"repair contains invalid key for {field_name}"

    unsupported_keys = sorted(key for key in _mapping_keys(repaired) if not _source_contains_key(candidate, key))
    if unsupported_keys:
        return f"repair introduced {len(unsupported_keys)} key(s) without source evidence"
    unsupported_values = [value for value in _string_values(repaired) if value and value not in candidate]
    if unsupported_values:
        return f"repair introduced {len(unsupported_values)} string value(s) without source evidence"
    return None


def _mapping_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return {str(key) for key in value} | {nested_key for item in value.values() for nested_key in _mapping_keys(item)}
    if isinstance(value, list):
        return {nested_key for item in value for nested_key in _mapping_keys(item)}
    return set()


def _string_values(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [item for nested in value.values() for item in _string_values(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _string_values(nested)]
    return [value] if isinstance(value, str) else []


def _shape_metrics(value: Any, depth: int = 1) -> tuple[int, int]:
    if isinstance(value, Mapping):
        metrics = [_shape_metrics(item, depth + 1) for item in value.values()]
    elif isinstance(value, list):
        metrics = [_shape_metrics(item, depth + 1) for item in value]
    else:
        return depth, 1
    return max([depth, *(item[0] for item in metrics)]), 1 + sum(item[1] for item in metrics)


def _source_contains_key(candidate: str, key: str) -> bool:
    tokens = {
        json.dumps(key, ensure_ascii=False),
        json.dumps(key, ensure_ascii=True),
        f"'{key}'",
    }
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
        tokens.add(key)
    return any(re.search(rf"{re.escape(token)}\s*:", candidate) for token in tokens)


def _decode_http_header(value: str) -> dict[str, object]:
    lines = value.splitlines()
    if not lines:
        return {}
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, raw_value = line.split(":", 1)
        normalized_name = name.strip().lower()
        normalized_value = raw_value.strip()
        if not normalized_name:
            continue
        headers.setdefault(normalized_name, []).append(normalized_value)
    result: dict[str, object] = {"start_line": lines[0].strip(), "headers": headers}
    forwarded_values = [item for name in ("x-forwarded-for", "x-real-ip") for value in headers.get(name, []) for item in _forwarded_chain(value)]
    if forwarded_values:
        result["forwarded_chain"] = list(dict.fromkeys(forwarded_values))
    return result


def _forwarded_chain(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _set_nested(target: dict[str, object], path: tuple[str, ...], value: object) -> None:
    current = target
    for segment in path[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[path[-1]] = value


def _strip_bracketed_prefix(message: str) -> tuple[str, dict[str, str]]:
    first_equals = message.find("=")
    closing = message.rfind("]", 0, first_equals) if first_equals >= 0 else message.rfind("]")
    if closing < 0:
        return message, {}
    prefix = message[: closing + 1]
    return message[closing + 1 :], {"prefix": prefix}


__all__ = [
    "DEFAULT_PINGAN_MESSAGE_PARSERS",
    "PingAnRawMessageParser",
    "parse_pingan_raw_message",
]
