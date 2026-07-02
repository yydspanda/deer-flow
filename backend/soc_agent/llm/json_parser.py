"""Parse and validate LLM JSON output for SOC analysis nodes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from json_repair import loads as repair_json_loads
from pydantic import ValidationError

from soc_agent.contracts import AnalysisResult
from soc_agent.core.validator import validate_analysis_result

ANALYSIS_JSON_PARSER_VERSION = "soc-analysis-json-parser-v1"

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)


class LLMOutputParseError(ValueError):
    """Raised when LLM output cannot become a valid ``AnalysisResult``."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        parser_version: str = ANALYSIS_JSON_PARSER_VERSION,
        repair_applied: bool = False,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.parser_version = parser_version
        self.repair_applied = repair_applied


@dataclass(frozen=True)
class ParsedAnalysisResult:
    """Validated analysis output plus parser audit metadata."""

    result: AnalysisResult
    parser_version: str = ANALYSIS_JSON_PARSER_VERSION
    repair_applied: bool = False
    repair_log: list[dict[str, Any]] = field(default_factory=list)
    candidate_text: str = ""


def parse_analysis_result_output(response_content: Any) -> ParsedAnalysisResult:
    """Parse LLM content into a domain-validated ``AnalysisResult``.

    This follows DeerFlow's conservative pattern first: extract text from modern
    content blocks, strip thinking/code fences, and accept a strict JSON object
    when one can be decoded. Only after strict parsing fails do we invoke
    ``json_repair``; the repaired object still has to pass schema and domain
    validation before it can enter runtime decision logic.
    """

    text = _strip_markdown_code_fence(_strip_think_blocks(_extract_text(response_content))).strip()
    if not text:
        raise LLMOutputParseError("LLM output is empty", stage="extract_text")

    strict = _parse_strict_json_object(text)
    if strict is not None:
        data, candidate_text = strict
        return ParsedAnalysisResult(
            result=_validate_analysis_result_data(data, repair_applied=False),
            repair_applied=False,
            candidate_text=candidate_text,
        )

    candidate_text = _extract_repair_candidate(text)
    repaired = _repair_json_object(candidate_text)
    return ParsedAnalysisResult(
        result=_validate_analysis_result_data(repaired.data, repair_applied=True),
        repair_applied=True,
        repair_log=repaired.log,
        candidate_text=candidate_text,
    )


@dataclass(frozen=True)
class _RepairedJson:
    data: dict[str, Any]
    log: list[dict[str, Any]]


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        pending_str_parts: list[str] = []

        def flush_pending_str_parts() -> None:
            if pending_str_parts:
                pieces.append("".join(pending_str_parts))
                pending_str_parts.clear()

        for block in content:
            if isinstance(block, str):
                pending_str_parts.append(block)
            elif isinstance(block, dict):
                flush_pending_str_parts()
                text_val = block.get("text")
                if isinstance(text_val, str):
                    pieces.append(text_val)

        flush_pending_str_parts()
        return "\n".join(pieces)
    return str(content)


def _strip_think_blocks(text: str) -> str:
    text = _THINK_BLOCK_RE.sub("", text)
    open_match = _OPEN_THINK_RE.search(text)
    if open_match:
        text = text[: open_match.start()]
    return text.strip()


def _strip_markdown_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _parse_strict_json_object(text: str) -> tuple[dict[str, Any], str] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        candidate = text[match.start() :]
        try:
            parsed, end = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and _looks_like_analysis_result_object(parsed):
            return parsed, candidate[:end]
    return None


def _looks_like_analysis_result_object(data: dict[str, Any]) -> bool:
    return {"verdict", "confidence", "summary", "evidence", "reason", "recommended_action"}.issubset(data)


def _extract_repair_candidate(text: str) -> str:
    start = text.find("{")
    if start == -1:
        return text
    end = text.rfind("}")
    if end == -1 or end < start:
        return text[start:]
    return text[start : end + 1]


def _repair_json_object(candidate_text: str) -> _RepairedJson:
    try:
        repaired, repair_log = repair_json_loads(
            candidate_text,
            logging=True,
            skip_json_loads=True,
        )
    except Exception as exc:  # noqa: BLE001 - normalize third-party parser failures
        raise LLMOutputParseError(
            f"LLM output JSON repair failed: {exc}",
            stage="json_repair",
            repair_applied=True,
        ) from exc

    if not isinstance(repaired, dict):
        raise LLMOutputParseError(
            "LLM output did not repair to a JSON object",
            stage="json_repair",
            repair_applied=True,
        )

    normalized_log = [item for item in repair_log if isinstance(item, dict)] if isinstance(repair_log, list) else []
    return _RepairedJson(data=repaired, log=normalized_log)


def _validate_analysis_result_data(data: dict[str, Any], *, repair_applied: bool) -> AnalysisResult:
    _validate_raw_analysis_shape(data, repair_applied=repair_applied)
    try:
        result = AnalysisResult.model_validate(data)
    except ValidationError as exc:
        raise LLMOutputParseError(
            f"LLM output failed AnalysisResult schema validation: {exc}",
            stage="schema_validation",
            repair_applied=repair_applied,
        ) from exc

    try:
        return validate_analysis_result(result)
    except Exception as exc:  # noqa: BLE001 - normalize domain validation failures
        raise LLMOutputParseError(
            f"LLM output failed analysis domain validation: {exc}",
            stage="domain_validation",
            repair_applied=repair_applied,
        ) from exc


def _validate_raw_analysis_shape(data: dict[str, Any], *, repair_applied: bool) -> None:
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise LLMOutputParseError(
            "LLM output confidence must be a JSON number",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise LLMOutputParseError(
            "LLM output evidence must be a non-empty JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
