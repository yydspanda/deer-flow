"""Bounded LLM generator for review-only Memory Business Lesson drafts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from json_repair import loads as repair_json_loads
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.contracts import (
    SocMemoryBusinessLesson,
    SocMemoryBusinessLessonDraft,
    SocMemoryBusinessLessonDraftProvenance,
    SocMemoryBusinessLessonDraftRationale,
    SocMemoryCandidate,
    SocMemoryLessonDraftSource,
    Verdict,
)
from soc_agent.llm.analyzer import LLMChatClient, coerce_chat_response
from soc_agent.memory.lessons import (
    memory_lesson_applicability_conditions,
    memory_lesson_invalidation_conditions,
)
from soc_agent.prompts.memory_lesson import (
    MEMORY_LESSON_DRAFT_PROMPT_VERSION,
    MEMORY_LESSON_MODEL_OUTPUT_SCHEMA_VERSION,
    build_memory_lesson_draft_prompt,
)
from soc_agent.utils.hashing import stable_hash

MEMORY_LESSON_DRAFTER_ID = "bounded-memory-business-lesson-drafter"
MAX_MEMORY_LESSON_RESPONSE_CHARS = 40_000

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
_URI_OR_DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9._-])(?:https?://)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}"
    r"(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~!$&'()*+,;=:@%/?#-]*)?",
)
_NAMESPACED_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9._-])[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+){2,}",
)
_FORBIDDEN_CONCLUSION_ACTION_PATTERNS = (
    re.compile(
        r"(?:应|应当|需要|必须|建议|可以|可|允许|直接|立即|自动|无需|不必|不得|禁止|不要|不做)"
        r".{0,12}(?:关闭|封禁|隔离|拒绝访问|抑制|批准|阻断|升级处理|授权(?:执行|处置|动作))"
    ),
    re.compile(
        r"(?:关闭|封禁|隔离|抑制|阻断)"
        r"(?:该|此|相关|目标|攻击|来源|告警|预警|事件|工单|IP|地址|主机|终端|账号|用户|进程|文件|域名|流量|连接|资产|请求|访问)"
    ),
    re.compile(r"(?:批准|授权)(?:执行|处置|动作|封禁|隔离|阻断|关闭)"),
    re.compile(r"升级处理"),
)
_SAFE_METADATA_KEYS = (
    "admission_wait_duration_ms",
    "client_total_duration_ms",
    "json_mode_requested",
    "provider_duration_ms",
    "response_reasoning_chars",
    "response_reasoning_present",
    "response_visible_text_chars",
    "thinking_enabled_requested",
)
_MODEL_OUTPUT_KEYS = frozenset(
    {
        "schema_version",
        "reviewer_verdict",
        "detection_scenario",
        "observed_event",
        "conclusion",
        "supporting_source_refs",
        "business_rationale",
        "generalization_boundaries",
        "invalidation_conditions",
        "handling_guidance",
        "uncertainties",
    }
)


class _BusinessRationaleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=5, max_length=4000)
    source_refs: list[str] = Field(min_length=1, max_length=12)

    @field_validator("statement")
    @classmethod
    def normalize_statement(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 5:
            raise ValueError("business rationale statement must be substantive")
        return normalized

    @field_validator("source_refs")
    @classmethod
    def normalize_source_refs(cls, values: list[str]) -> list[str]:
        return _normalize_refs(values)


class _MemoryBusinessLessonModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    reviewer_verdict: Verdict
    detection_scenario: str = Field(min_length=5, max_length=2000)
    observed_event: str = Field(min_length=5, max_length=4000)
    conclusion: str = Field(min_length=10, max_length=2000)
    supporting_source_refs: list[str] = Field(min_length=1, max_length=40)
    business_rationale: list[_BusinessRationaleItem] = Field(min_length=1, max_length=12)
    generalization_boundaries: list[str] = Field(min_length=1, max_length=12)
    invalidation_conditions: list[str] = Field(max_length=12)
    handling_guidance: list[str] = Field(min_length=1, max_length=12)
    uncertainties: list[str] = Field(max_length=12)

    @field_validator("schema_version")
    @classmethod
    def require_schema_version(cls, value: str) -> str:
        if value != MEMORY_LESSON_MODEL_OUTPUT_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {MEMORY_LESSON_MODEL_OUTPUT_SCHEMA_VERSION}")
        return value

    @field_validator("detection_scenario", "observed_event", "conclusion")
    @classmethod
    def normalize_event_summary(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 5:
            raise ValueError("memory lesson event summary must be substantive")
        return normalized

    @field_validator("supporting_source_refs")
    @classmethod
    def normalize_supporting_refs(cls, values: list[str]) -> list[str]:
        return _normalize_refs(values)

    @field_validator(
        "generalization_boundaries",
        "handling_guidance",
    )
    @classmethod
    def normalize_required_text_items(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(" ".join(str(value).split()) for value in values if str(value).strip()))
        if not normalized:
            raise ValueError("memory lesson draft sections must not be empty")
        if any(len(value) < 5 or len(value) > 4000 for value in normalized):
            raise ValueError("memory lesson draft items must be 5-4000 characters")
        return normalized

    @field_validator("invalidation_conditions", "uncertainties")
    @classmethod
    def normalize_optional_text_items(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(" ".join(str(value).split()) for value in values if str(value).strip()))
        if any(len(value) < 5 or len(value) > 4000 for value in normalized):
            raise ValueError("optional memory lesson items must be 5-4000 characters")
        return normalized

    @model_validator(mode="after")
    def require_rationale_refs_in_supporting_set(
        self,
    ) -> _MemoryBusinessLessonModelOutput:
        rationale_refs = {ref for item in self.business_rationale for ref in item.source_refs}
        if not rationale_refs <= set(self.supporting_source_refs):
            raise ValueError("business rationale source_refs must be included in supporting_source_refs")
        return self


class JsonLLMMemoryLessonDrafter:
    """Generate one non-persisted draft from one quality-gated candidate."""

    def __init__(
        self,
        *,
        client: LLMChatClient,
        model_name: str,
        output_retry_attempts: int = 1,
    ) -> None:
        if not model_name.strip():
            raise ValueError("memory lesson drafter model_name is required")
        if output_retry_attempts not in {0, 1}:
            raise ValueError("memory lesson output_retry_attempts must be 0 or 1")
        self._client = client
        self.model_name = model_name.strip()
        self.prompt_version = MEMORY_LESSON_DRAFT_PROMPT_VERSION
        self._output_retry_attempts = output_retry_attempts

    def draft(
        self,
        candidate: SocMemoryCandidate,
        *,
        reviewer_verdict: Verdict,
        reviewer_context: str | None = None,
    ) -> SocMemoryBusinessLessonDraft:
        if candidate.applicability is None:
            raise ValueError("memory business lesson drafting requires machine applicability")
        prompt = build_memory_lesson_draft_prompt(
            candidate,
            reviewer_verdict=reviewer_verdict,
            reviewer_context=reviewer_context,
        )
        messages = prompt.messages()
        prompt_hash = stable_hash({"messages": messages})
        primary_response = coerce_chat_response(
            self._client.complete(messages, model_name=self.model_name),
            messages=messages,
        )
        valid_refs = {item.source_ref for item in prompt.source_catalog}
        responses = [primary_response]
        repair_actions: list[str] = []
        repair_prompt_hash: str | None = None
        try:
            output, parse_repair_actions = _parse_model_output(primary_response.content)
            _validate_output_contract(
                output,
                reviewer_verdict=reviewer_verdict,
                valid_refs=valid_refs,
                source_catalog=prompt.source_catalog,
            )
            repair_actions.extend(parse_repair_actions)
        except ValueError as primary_error:
            if self._output_retry_attempts == 0:
                raise
            repair_messages = _build_output_repair_messages(
                prompt_context=prompt.context,
                response_schema=prompt.response_schema,
                invalid_output=_extract_text(primary_response.content),
                validation_error=str(primary_error),
            )
            repair_prompt_hash = stable_hash({"messages": repair_messages})
            repaired_response = coerce_chat_response(
                self._client.complete(
                    repair_messages,
                    model_name=self.model_name,
                ),
                messages=repair_messages,
            )
            responses.append(repaired_response)
            output, parse_repair_actions = _parse_model_output(
                repaired_response.content,
            )
            _validate_output_contract(
                output,
                reviewer_verdict=reviewer_verdict,
                valid_refs=valid_refs,
                source_catalog=prompt.source_catalog,
            )
            repair_actions.extend(["provider_output_repair", *parse_repair_actions])
        response = responses[-1]
        lesson = SocMemoryBusinessLesson(
            schema_version="soc.memory_business_lesson.v2",
            detection_scenario=output.detection_scenario,
            observed_event=output.observed_event,
            conclusion=output.conclusion,
            business_rationale=[item.statement for item in output.business_rationale],
            applicability_conditions=memory_lesson_applicability_conditions(candidate.applicability),
            generalization_boundaries=output.generalization_boundaries,
            invalidation_conditions=memory_lesson_invalidation_conditions(
                output.invalidation_conditions,
            ),
            handling_guidance=output.handling_guidance,
        )
        response_hash = stable_hash({"model_output": output.model_dump(mode="json")})
        return SocMemoryBusinessLessonDraft(
            candidate_id=candidate.candidate_id,
            reviewer_verdict=reviewer_verdict,
            lesson=lesson,
            supporting_source_refs=output.supporting_source_refs,
            rationale_sources=[
                SocMemoryBusinessLessonDraftRationale(
                    statement=item.statement,
                    source_refs=item.source_refs,
                )
                for item in output.business_rationale
            ],
            source_catalog=list(prompt.source_catalog),
            uncertainties=output.uncertainties,
            provenance=SocMemoryBusinessLessonDraftProvenance(
                generator_id=MEMORY_LESSON_DRAFTER_ID,
                model_name=response.model_name or self.model_name,
                prompt_version=prompt.prompt_version,
                prompt_hash=prompt_hash,
                response_hash=response_hash,
                repair_applied=bool(repair_actions),
                repair_actions=repair_actions,
                repair_prompt_hash=repair_prompt_hash,
                provider_call_count=len(responses),
                output_repair_call_count=len(responses) - 1,
                usage=_merge_usage(*(item.usage for item in responses)),
                metadata=_bounded_metadata(response.metadata),
            ),
        )


def _validate_output_contract(
    output: _MemoryBusinessLessonModelOutput,
    *,
    reviewer_verdict: Verdict,
    valid_refs: set[str],
    source_catalog: Sequence[SocMemoryLessonDraftSource],
) -> None:
    if output.reviewer_verdict is not reviewer_verdict:
        raise ValueError("memory lesson draft reviewer_verdict does not match the authenticated reviewer selection")
    returned_refs = set(output.supporting_source_refs)
    rationale_refs = {ref for item in output.business_rationale for ref in item.source_refs}
    returned_refs.update(rationale_refs)
    missing = sorted(returned_refs - valid_refs)
    if missing:
        raise ValueError("memory lesson draft contains unresolved source refs: " + ", ".join(missing))
    required_reviewer_refs = {item.source_ref for item in source_catalog if item.source_kind in {"reviewer_verdict", "reviewer_context"}}
    missing_reviewer_refs = sorted(required_reviewer_refs - rationale_refs)
    if missing_reviewer_refs:
        raise ValueError("memory lesson draft rationale does not cite reviewer-owned input: " + ", ".join(missing_reviewer_refs))
    if any(pattern.search(output.conclusion) for pattern in _FORBIDDEN_CONCLUSION_ACTION_PATTERNS):
        raise ValueError("memory lesson conclusion contains action language reserved for handling_guidance")
    source_text = "\n".join(str(item.value) for item in source_catalog)
    output_text = "\n".join(
        [
            output.detection_scenario,
            output.observed_event,
            output.conclusion,
            *(item.statement for item in output.business_rationale),
            *output.generalization_boundaries,
            *output.invalidation_conditions,
            *output.handling_guidance,
            *output.uncertainties,
        ]
    )
    identifiers = {match.group(0) for pattern in (_URI_OR_DOMAIN_RE, _NAMESPACED_IDENTIFIER_RE) for match in pattern.finditer(output_text)}
    unknown_identifiers = sorted(identifier for identifier in identifiers if identifier not in source_text)
    if unknown_identifiers:
        raise ValueError("memory lesson draft contains literal identifiers absent from source catalog: " + ", ".join(unknown_identifiers[:10]))


def _build_output_repair_messages(
    *,
    prompt_context: Mapping[str, Any],
    response_schema: Mapping[str, Any],
    invalid_output: str,
    validation_error: str,
) -> list[dict[str, str]]:
    bounded_invalid_output = invalid_output[:MAX_MEMORY_LESSON_RESPONSE_CHARS]
    repair_request = {
        "schema_version": "soc.memory_business_lesson_output_repair_request.v1",
        "validation_error": validation_error[:4000],
        "invalid_output": bounded_invalid_output,
        "lesson_context": prompt_context,
        "response_schema": response_schema,
    }
    return [
        {
            "role": "system",
            "content": (
                "Repair one invalid SOC Business Lesson JSON object. Return JSON only. "
                "Use only the supplied bounded lesson_context and exact D-* aliases. "
                "Do not add authority, applicability, alert facts, or identifiers. "
                "The conclusion must contain facts and verdict only. Move close, suppress, block, isolate, "
                "approval, execution-authorization, and other action instructions to handling_guidance. "
                "A source-backed factual authorization status may remain factual, but must not grant an action. "
                "Preserve valid business meaning from invalid_output, fill only schema-required sections, "
                "and obey additionalProperties=false."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                repair_request,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            ),
        },
    ]


def _parse_model_output(
    content: Any,
) -> tuple[_MemoryBusinessLessonModelOutput, list[str]]:
    text = _strip_fence(_strip_think(_extract_text(content))).strip()
    if not text:
        raise ValueError("memory lesson drafter output is empty")
    if len(text) > MAX_MEMORY_LESSON_RESPONSE_CHARS:
        raise ValueError("memory lesson drafter output exceeds size limit")
    candidate = _extract_json_candidate(text)
    try:
        payload = json.loads(candidate)
        repair_actions: list[str] = []
    except json.JSONDecodeError:
        payload = repair_json_loads(candidate, skip_json_loads=True)
        repair_actions = ["json_syntax_repair"]
    if not isinstance(payload, dict):
        raise ValueError("memory lesson drafter output must be a JSON object")
    for key in sorted(set(payload) - _MODEL_OUTPUT_KEYS):
        if payload[key] not in (None, "", [], {}):
            continue
        payload.pop(key)
        repair_actions.append(f"drop_empty_unknown_field:{key}"[:256])
    _normalize_supporting_refs_from_rationale(payload, repair_actions)
    return _MemoryBusinessLessonModelOutput.model_validate(payload), repair_actions


def _normalize_supporting_refs_from_rationale(
    payload: dict[str, Any],
    repair_actions: list[str],
) -> None:
    """Canonicalize a redundant citation index without changing cited facts."""

    supporting_refs = payload.get("supporting_source_refs")
    rationale_items = payload.get("business_rationale")
    if not isinstance(supporting_refs, list) or not isinstance(rationale_items, list):
        return
    if any(not isinstance(value, str) or re.fullmatch(r"D-[0-9]{3}", value.strip()) is None for value in supporting_refs):
        return

    rationale_refs: list[str] = []
    for item in rationale_items:
        if not isinstance(item, Mapping) or not isinstance(item.get("source_refs"), list):
            return
        source_refs = item["source_refs"]
        if any(not isinstance(value, str) or re.fullmatch(r"D-[0-9]{3}", value.strip()) is None for value in source_refs):
            return
        rationale_refs.extend(value.strip() for value in source_refs)

    canonical_refs = list(dict.fromkeys(rationale_refs))
    normalized_supporting_refs = list(dict.fromkeys(value.strip() for value in supporting_refs))
    if canonical_refs and canonical_refs != normalized_supporting_refs:
        payload["supporting_source_refs"] = canonical_refs
        repair_actions.append("normalize_supporting_refs_to_rationale_union")


def _normalize_refs(values: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not normalized or any(re.fullmatch(r"D-[0-9]{3}", value) is None for value in normalized):
        raise ValueError("memory lesson model refs must use exact D-001 aliases")
    return normalized


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        values: list[str] = []
        for item in content:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                values.append(str(item["text"]))
        return "\n".join(values)
    return str(content)


def _strip_think(text: str) -> str:
    value = _THINK_BLOCK_RE.sub("", text)
    open_match = _OPEN_THINK_RE.search(value)
    return value[: open_match.start()] if open_match else value


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _extract_json_candidate(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0:
        return text
    return text[start:] if end < start else text[start : end + 1]


def _merge_usage(
    *usage_items: Mapping[str, Any],
) -> dict[str, int | float | str]:
    result: dict[str, int | float | str] = {}
    for usage in usage_items:
        for raw_key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                continue
            key = str(raw_key)[:128]
            current = result.get(key)
            if isinstance(value, (int, float)) and isinstance(current, (int, float)):
                result[key] = current + value
            elif current is None:
                result[key] = value
    return result


def _bounded_metadata(
    metadata: Mapping[str, Any],
) -> dict[str, int | float | str | bool | None]:
    result: dict[str, int | float | str | bool | None] = {}
    for key in _SAFE_METADATA_KEYS:
        value = metadata.get(key)
        if value is None or (isinstance(value, (int, float, str, bool)) and not isinstance(value, (list, dict))):
            result[key] = value
    measurement = metadata.get("usage_measurement")
    if isinstance(measurement, Mapping):
        for source_key, target_key in (
            ("status", "usage_measurement_status"),
            ("method", "usage_estimation_method"),
        ):
            value = measurement.get(source_key)
            if isinstance(value, str):
                result[target_key] = value[:128]
    return result


__all__ = [
    "MEMORY_LESSON_DRAFTER_ID",
    "MAX_MEMORY_LESSON_RESPONSE_CHARS",
    "JsonLLMMemoryLessonDrafter",
]
