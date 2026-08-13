"""Bounded LLM advisor for tenant-owned operational policy Skills."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from json_repair import loads as repair_json_loads

from soc_agent.contracts import (
    AnalysisRun,
    TenantDispositionPolicy,
    TenantPolicyAdvice,
    TenantPolicyAdvisorProvenance,
    TenantPolicyAdvisorResult,
    TenantPolicyAdvisorStatus,
    TenantPolicyEvaluationStatus,
)
from soc_agent.llm.analyzer import LLMChatClient, LLMChatResponse, coerce_chat_response
from soc_agent.prompts.tenant_policy import (
    TENANT_POLICY_ADVISOR_PROMPT_VERSION,
    build_tenant_policy_advisor_prompt,
)
from soc_agent.utils.hashing import stable_hash

TENANT_POLICY_ADVISOR_ID = "bounded-tenant-policy-skill-advisor"
MAX_TENANT_POLICY_SKILL_CHARS = 40_000
MAX_TENANT_POLICY_RESPONSE_CHARS = 40_000

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)


class LLMTenantPolicyAdvisor:
    """Evaluate a reviewed tenant policy Skill after deterministic no-match."""

    def __init__(
        self,
        *,
        client: LLMChatClient,
        model_name: str,
        skill_path: str | Path,
    ) -> None:
        if not model_name.strip():
            raise ValueError("tenant policy advisor model_name is required")
        self._client = client
        self._model_name = model_name.strip()
        self._skill_path = Path(skill_path).expanduser().resolve()
        self._skill_content = self._skill_path.read_text(encoding="utf-8")
        if not self._skill_content.strip():
            raise ValueError("tenant policy Skill must be non-empty")
        if len(self._skill_content) > MAX_TENANT_POLICY_SKILL_CHARS:
            raise ValueError(f"tenant policy Skill exceeds {MAX_TENANT_POLICY_SKILL_CHARS} characters")
        self._skill_name, self._skill_version = _skill_identity(self._skill_content)
        self._skill_hash = stable_hash({"content": self._skill_content})

    def advise(
        self,
        policy: TenantDispositionPolicy,
        run: AnalysisRun,
    ) -> TenantPolicyAdvisorResult:
        failure_stage = "prompt_build"
        response: LLMChatResponse | None = None
        prompt_hash = stable_hash(
            {
                "prompt_version": TENANT_POLICY_ADVISOR_PROMPT_VERSION,
                "skill_hash": self._skill_hash,
                "run_id": run.run_id,
                "policy_id": policy.policy_id,
                "policy_version": policy.policy_version,
            }
        )
        try:
            prompt = build_tenant_policy_advisor_prompt(
                policy,
                run,
                skill_content=self._skill_content,
                skill_name=self._skill_name,
                skill_version=self._skill_version,
            )
            prompt_hash = stable_hash({"messages": prompt.messages()})
            failure_stage = "model_call"
            prompt_messages = prompt.messages()
            response = coerce_chat_response(
                self._client.complete(
                    prompt_messages,
                    model_name=self._model_name,
                ),
                messages=prompt_messages,
            )
            failure_stage = "output_parse"
            advice, repair_applied = _parse_advice(response.content)
            failure_stage = "reference_validation"
            _validate_references(advice, run)
            response_hash = stable_hash({"advice": advice.model_dump(mode="json", exclude_none=True)})
            return TenantPolicyAdvisorResult(
                advice=advice,
                provenance=TenantPolicyAdvisorProvenance(
                    advisor_id=TENANT_POLICY_ADVISOR_ID,
                    status=TenantPolicyAdvisorStatus.COMPLETED,
                    model_name=response.model_name or self._model_name,
                    prompt_version=prompt.prompt_version,
                    prompt_hash=prompt_hash,
                    skill_name=self._skill_name,
                    skill_version=self._skill_version,
                    skill_source_ref=str(self._skill_path),
                    skill_hash=self._skill_hash,
                    response_hash=response_hash,
                    repair_applied=repair_applied,
                    usage=_bounded_usage(response.usage),
                    **_measurement_provenance(response.metadata),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - this optional layer must fail closed
            return TenantPolicyAdvisorResult(
                advice=TenantPolicyAdvice(
                    evaluation_status=TenantPolicyEvaluationStatus.NO_MATCH,
                    summary=("租户策略推理不可用；保留 Runtime 与 Memory 阶段结果，不自动改变处置或复核要求。"),
                    rationale=["可选策略 Skill 调用或结构校验失败，系统按 fail-closed 处理。"],
                ),
                provenance=TenantPolicyAdvisorProvenance(
                    advisor_id=TENANT_POLICY_ADVISOR_ID,
                    status=TenantPolicyAdvisorStatus.FAILED_CLOSED,
                    model_name=self._model_name,
                    prompt_version=TENANT_POLICY_ADVISOR_PROMPT_VERSION,
                    prompt_hash=prompt_hash,
                    skill_name=self._skill_name,
                    skill_version=self._skill_version,
                    skill_source_ref=str(self._skill_path),
                    skill_hash=self._skill_hash,
                    usage=(_bounded_usage(response.usage) if response is not None else {}),
                    **_measurement_provenance(response.metadata if response is not None else getattr(exc, "soc_llm_client_measurement", {})),
                    error_code=f"{failure_stage}.{type(exc).__name__}"[:128],
                ),
            )


def _parse_advice(content: Any) -> tuple[TenantPolicyAdvice, bool]:
    text = _strip_fence(_strip_think(_extract_text(content))).strip()
    if not text:
        raise ValueError("tenant policy advisor output is empty")
    if len(text) > MAX_TENANT_POLICY_RESPONSE_CHARS:
        raise ValueError("tenant policy advisor output exceeds size limit")
    candidate = _extract_json_candidate(text)
    try:
        payload = json.loads(candidate)
        repair_applied = False
    except json.JSONDecodeError:
        payload = repair_json_loads(
            candidate,
            skip_json_loads=True,
        )
        repair_applied = True
    if not isinstance(payload, dict):
        raise ValueError("tenant policy advisor output must be a JSON object")
    return TenantPolicyAdvice.model_validate(payload), repair_applied


def _validate_references(advice: TenantPolicyAdvice, run: AnalysisRun) -> None:
    request = run.llm_analysis_request
    analysis = run.analysis
    if request is None or analysis is None:
        raise ValueError("tenant policy advice requires Runtime references")
    valid_evidence = {item.evidence_ref for item in request.evidence_catalog}
    valid_reasoning = {item.reasoning_id for item in analysis.reasoning}
    valid_context = {item.context_ref for item in request.context_catalog}
    missing_evidence = sorted(set(advice.evidence_refs) - valid_evidence)
    missing_reasoning = sorted(set(advice.reasoning_refs) - valid_reasoning)
    missing_context = sorted(set(advice.context_refs) - valid_context)
    if missing_evidence or missing_reasoning or missing_context:
        raise ValueError(f"tenant policy advice contains unresolved references; evidence={missing_evidence}, reasoning={missing_reasoning}, context={missing_context}")


def _skill_identity(content: str) -> tuple[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("tenant policy Skill requires YAML frontmatter")
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError("tenant policy Skill frontmatter is not closed") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("'\"")
    name = metadata.get("name", "").strip()
    version = metadata.get("version", "").strip()
    if not name or not version:
        raise ValueError("tenant policy Skill requires name and version metadata")
    return name, version


def _measurement_provenance(metadata: Mapping[str, Any]) -> dict[str, Any]:
    measurement = metadata.get("usage_measurement")
    if not isinstance(measurement, Mapping):
        measurement = {}
    status = str(measurement.get("status") or "unavailable")
    if status not in {"reported", "estimated", "mixed", "unavailable"}:
        status = "unavailable"
    method = measurement.get("method")
    values: dict[str, Any] = {
        "usage_measurement_status": status,
        "usage_estimation_method": (str(method)[:128] if status in {"estimated", "mixed"} and method is not None else None),
        "usage_is_estimated": status in {"estimated", "mixed"},
    }
    for key in (
        "admission_wait_duration_ms",
        "provider_duration_ms",
        "client_total_duration_ms",
    ):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[key] = float(value)
    return values


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


def _bounded_usage(usage: Mapping[str, Any]) -> dict[str, int | float | str]:
    return {str(key)[:128]: value for key, value in usage.items() if isinstance(value, (int, float, str)) and not isinstance(value, bool)}


__all__ = [
    "LLMTenantPolicyAdvisor",
    "MAX_TENANT_POLICY_RESPONSE_CHARS",
    "MAX_TENANT_POLICY_SKILL_CHARS",
    "TENANT_POLICY_ADVISOR_ID",
]
