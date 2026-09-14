"""Source-selected semantic review. No vendor parsing, verdicts, or Memory writes."""

from __future__ import annotations

import ipaddress
import json
from collections import defaultdict
from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from soc_agent.contracts import (
    AlertEntitySet,
    AlertInput,
    CanonicalFieldProvenance,
    EvidenceInputPolicy,
    FileObservationRef,
    FileObservationRelation,
    NormalizationAssistRequest,
    NormalizationAssistResult,
    NormalizationFactChange,
    SensitiveEvidenceMode,
)
from soc_agent.contracts.normalization import NormalizationSource
from soc_agent.llm.analyzer import LLMChatClient, coerce_chat_response
from soc_agent.llm.json_parser import _extract_text, _strip_markdown_code_fence, _strip_think_blocks
from soc_agent.normalizers.semantic_observations import apply_observation_changes, build_object_catalog, collect_observation_changes
from soc_agent.pipeline.analysis_context import _resolve_path, _safe_string_fallback, _sanitize_value
from soc_agent.pipeline.encoded_context import compact_encoded_spans
from soc_agent.prompts.normalization import NORMALIZATION_PROMPT_VERSION, NORMALIZATION_TARGETS, build_normalization_prompt
from soc_agent.utils.hashing import stable_hash


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(max_length=100)
    value: str = Field(min_length=1, max_length=8000)
    source_quote: str = Field(min_length=1, max_length=10000)
    reason: str = Field(min_length=1, max_length=1000)
    quote_start: int | None = Field(default=None, ge=0)


class JsonLLMNormalizationReviewer:
    step_name = "normalization_assist"
    prompt_version = NORMALIZATION_PROMPT_VERSION

    def __init__(
        self,
        *,
        client: LLMChatClient,
        model_name: str,
        mode: Literal["shadow", "apply"] = "apply",
        configuration_hash: str = "injected-client",
        sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.FULL,
        reference_validation_enabled: bool = False,
    ) -> None:
        if mode not in {"shadow", "apply"}:
            raise ValueError("normalization review mode must be shadow or apply")
        self.client = client
        self.model_name = model_name
        self.mode = mode
        self.sensitive_evidence_mode = sensitive_evidence_mode
        self.reference_validation_enabled = reference_validation_enabled
        self.configuration_hash = stable_hash(
            {
                "model": model_name,
                "mode": mode,
                "reference_validation_enabled": reference_validation_enabled,
                "configuration": configuration_hash,
                "prompt": build_normalization_prompt(NormalizationAssistRequest(alert_id="", source={}, detection={}, configuration_hash="", reference_validation_enabled=reference_validation_enabled))[0],
                "sensitive_mode": sensitive_evidence_mode,
            }
        )

    def prepare(self, alert: AlertInput) -> NormalizationAssistRequest:
        # Source choice is adapter-owned. Never fall back to the whole vendor envelope.
        draft = AlertEntitySet()
        omitted_draft_paths = []
        for target in NORMALIZATION_TARGETS:
            _, section, key = target.split(".")
            value = getattr(getattr(alert.entities, section), key)
            bounded = value[:4096] if isinstance(value, str) else [item[:64] for item in value[:100]] if isinstance(value, list) else value
            if bounded != value:
                omitted_draft_paths.append(target)
            setattr(getattr(draft, section), key, bounded)
        request = NormalizationAssistRequest(
            alert_id=alert.alert_id, source=alert.source, detection=alert.detection, adapter_entities=draft, configuration_hash=self.configuration_hash, reference_validation_enabled=self.reference_validation_enabled
        )
        request.omitted_draft_paths = omitted_draft_paths
        request.adapter_snapshot_hash = stable_hash(
            {
                "entities": alert.entities.model_dump(mode="json"),
                "policy": alert.extensions.get("evidence_input_policy"),
                "parsers": [(item.get("parser_name"), item.get("parser_version")) for item in alert.extensions.get("parsed_raw_messages", []) if isinstance(item, dict)],
            }
        )
        try:
            policy = EvidenceInputPolicy.model_validate(alert.extensions.get("evidence_input_policy", {}))
        except ValidationError:
            return request.model_copy(update={"skip_reason": "未声明可独立核对的原始证据来源。"})
        request.source_path = policy.selected_input_path
        request.source_layer = policy.selected_layer
        request.source_trust = policy.trust_level
        request.omitted_source_paths = policy.supplementary_input_paths[:200]
        request.omitted_source_count = len(policy.supplementary_input_paths)
        value = _resolve_path(alert.raw, request.source_path) if request.source_path else None
        if value is None or value == "" or value == {} or value == []:
            request.skip_reason = "选定的原始证据为空。"
            return request
        if self.sensitive_evidence_mode is SensitiveEvidenceMode.REDACT:
            value = _safe_string_fallback(value) if isinstance(value, str) else _sanitize_value(value)
            request.adapter_entities = type(draft).model_validate(_sanitize_value(draft.model_dump(mode="json")))
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        text, spans = compact_encoded_spans(text)
        request.encoded_span_count = len(spans)
        request.input_truncated = len(text) > 48_000
        request.source_text = text[:48_000]
        request.sources = [NormalizationSource(source_id="L0", source_path=request.source_path, text=request.source_text, truncated=request.input_truncated)]
        remaining = 48_000 - len(request.source_text)
        seen = {text}
        unchecked = []
        for path in policy.supplementary_input_paths:
            value = _resolve_path(alert.raw, path)
            if not isinstance(value, str) or not value.strip():
                unchecked.append(path)
                continue
            if self.sensitive_evidence_mode is SensitiveEvidenceMode.REDACT:
                value = _safe_string_fallback(value)
            value, spans = compact_encoded_spans(value)
            if value in seen:
                request.deduplicated_source_count += 1
                continue
            seen.add(value)
            if remaining < 256 or len(request.sources) >= 8:
                unchecked.append(path)
                continue
            length = min(remaining, len(value))
            request.sources.append(NormalizationSource(source_id=f"L{len(request.sources)}", source_path=path, text=value[:length], truncated=length < len(value)))
            request.encoded_span_count += len(spans)
            remaining -= length
        request.omitted_source_paths = unchecked[:200]
        request.omitted_source_count = len(unchecked)
        request.scope = "selected_evidence_sources" if len(request.sources) > 1 else "selected_primary_evidence"
        omitted_objects = []
        request.object_catalog = build_object_catalog(alert, request.sources, omitted_paths=omitted_objects)
        request.omitted_object_paths = omitted_objects[:200]
        request.omitted_object_count = len(omitted_objects)
        if self.sensitive_evidence_mode is SensitiveEvidenceMode.REDACT:
            request.object_catalog = _sanitize_value(request.object_catalog)
        semantics = alert.extensions.get("source_field_semantics", [])
        if isinstance(semantics, list):
            remaining = 12000
            for item in semantics:
                size = len(json.dumps(item, ensure_ascii=False))
                if not isinstance(item, dict) or size > min(2000, remaining) or len(request.source_semantics) >= 40:
                    request.omitted_semantic_count += 1
                    continue
                request.source_semantics.append(item)
                remaining -= size
        return request

    def review(self, alert: AlertInput, request: NormalizationAssistRequest) -> NormalizationAssistResult:
        report = NormalizationAssistResult(status="unchanged", mode=self.mode, request_hash=stable_hash(request.model_dump(mode="json")), model_name=self.model_name, prompt_version=self.prompt_version)
        if request.skip_reason:
            report.status = "skipped"
            report.issues = [request.skip_reason]
            report.metadata = {"provider_call_count": 0, "reference_validation_enabled": request.reference_validation_enabled}
            return report
        messages = build_normalization_prompt(request)
        report.metadata = {"provider_call_count": 1, "reference_validation_enabled": request.reference_validation_enabled}
        stage = "provider_call"
        try:
            response = coerce_chat_response(self.client.complete(messages, model_name=self.model_name), messages=messages)
            report.metadata.update({**response.metadata, "model_name": response.model_name or self.model_name, "usage": dict(response.usage)})
            stage = "parse_output"
            if response.metadata.get("finish_reason") == "length":
                report.metadata["error_code"] = "output_length_limit"
                raise ValueError("output_length_limit")
            text = _strip_markdown_code_fence(_strip_think_blocks(_extract_text(response.content))).strip()
            report.metadata["response_sha256"] = sha256(text.encode("utf-8")).hexdigest()
            payload = _read_output(text, report)
            facts = payload.get("facts", [])
            if facts and any(payload.get(k) for k in ("objects", "events", "additional_facts")):
                report.metadata["error_code"] = "mixed_legacy_and_observation_output"
                raise ValueError("mixed_legacy_and_observation_output")
            stage = "merge_proposals"
            _collect_changes(alert, request, facts, report)
            collect_observation_changes(alert, request, payload, report)
        except Exception as exc:  # noqa: BLE001 - this auxiliary result must not erase a usable Base
            report.status = "failed"
            report.changes = []
            report.observation_changes = []
            report.issues = [f"语义核对未完成：{type(exc).__name__}；保留 Adapter 结果。"]
            report.metadata.update(getattr(exc, "soc_llm_client_measurement", {}))
            report.metadata["error_type"] = type(exc).__name__
            timeout = isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
            report.metadata["failure_stage"] = stage
            report.metadata.setdefault("error_code", "provider_timeout" if timeout else "provider_unavailable" if stage == "provider_call" else "proposal_processing_error")
            report.metadata["failure_kind"] = "analyzer_timeout" if timeout else "analyzer_output_invalid" if isinstance(exc, (ValueError, ValidationError)) else "analyzer_unavailable"
            report.metadata["failure_retryable"] = not isinstance(exc, (ValueError, ValidationError))
            report.issues = [f"语义核对未完成：{report.metadata['error_code']}（{type(exc).__name__}）；保留 Adapter 结果。"]
            return report
        if request.input_truncated or any(s.truncated for s in request.sources):
            report.issues.append("当前选定日志超过输入长度，尾部未核对。")
        if request.omitted_source_count:
            report.issues.append(f"本次另有 {request.omitted_source_count} 条补充消息未核对。")
        if request.omitted_draft_paths or request.omitted_semantic_count:
            report.issues.append("本次核对草稿或字段语义说明存在限长省略，具体范围见核对输入记录。")
        if request.omitted_object_count:
            report.issues.append(f"已有对象目录中 {request.omitted_object_count} 项超过核对预算，具体路径见核对输入记录。")
        report.issues = report.issues[:40]
        changed = bool(report.changes or report.observation_changes)
        report.status = "partial" if report.issues else ("shadow" if changed and self.mode == "shadow" else "applied" if changed else "unchanged")
        return report


def _read_output(text: str, report: NormalizationAssistResult) -> dict:
    """Isolate malformed sections without guessing facts or incomplete JSON values."""
    if len(text) > 120_000:
        report.metadata["error_code"] = "output_size_limit"
        raise ValueError("output_size_limit")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        report.metadata.update({"error_code": "invalid_json", "json_error": {"line": exc.lineno, "column": exc.colno, "position": exc.pos, "message": exc.msg}})
        raise
    allowed = {"facts", "objects", "events", "additional_facts", "unresolved"}
    if not isinstance(payload, dict) or not (allowed & payload.keys()):
        report.metadata["error_code"] = "invalid_output_envelope"
        raise ValueError("invalid_output_envelope")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        report.metadata["ignored_output_fields"] = [key[:100] for key in unknown[:20]]
    result = {}
    for key in sorted(allowed):
        value = payload.get(key)
        if value is None:
            result[key] = []
            continue
        if not isinstance(value, list):
            report.metadata.setdefault("section_errors", []).append(f"invalid_{key}_list")
            report.issues.append(f"{key} 区块不是数组，未采用；其他有效区块继续处理。")
            result[key] = []
            continue
        limit = 20 if key == "unresolved" else 40
        if len(value) > limit:
            report.metadata.setdefault("section_errors", []).append(f"{key}_item_limit")
            report.issues.append(f"{key} 超过 {limit} 项，尾部未采用。")
        result[key] = value[:limit]
    unresolved = result["unresolved"]
    report.issues.extend(item[:1000] for item in unresolved if isinstance(item, str))
    if any(not isinstance(item, str) for item in unresolved):
        report.metadata.setdefault("section_errors", []).append("invalid_unresolved_item")
        report.issues.append("未解释问题包含非文本内容；不影响其他有效对象。")
    return result


def _collect_changes(alert: AlertInput, request: NormalizationAssistRequest, items: list, report: NormalizationAssistResult) -> None:
    grouped: dict[str, list[tuple[_Fact, int | None]]] = defaultdict(list)
    try:
        json.loads(request.source_text)
        json_source = True
    except ValueError:
        json_source = False
    for index, item in enumerate(items):
        try:
            fact = _Fact.model_validate(item)
            if fact.target not in NORMALIZATION_TARGETS or "<ENCODED:" in fact.value or "[REDACTED]" in fact.value:
                raise ValueError("unsupported_target_or_omitted_value")
            start = None
            if request.reference_validation_enabled:
                start = fact.quote_start
                if start is None:
                    count = request.source_text.count(fact.source_quote)
                    if count != 1:
                        raise ValueError("missing_quote" if count == 0 else "ambiguous_quote")
                    start = request.source_text.index(fact.source_quote)
                if request.source_text[start : start + len(fact.source_quote)] != fact.source_quote:
                    raise ValueError("missing_quote")
                if fact.value not in fact.source_quote and not (json_source and json.dumps(fact.value, ensure_ascii=False)[1:-1] in fact.source_quote):
                    raise ValueError("missing_value")
            if fact.target.endswith("ip_addresses"):
                ipaddress.ip_address(fact.value)
            elif fact.target.endswith("process_id"):
                if not fact.value.isdecimal():
                    raise ValueError("invalid_pid")
            elif fact.target.endswith("_path"):
                if not (PureWindowsPath(fact.value).is_absolute() or PurePosixPath(fact.value).is_absolute()):
                    raise ValueError("not_absolute_path")
            grouped[fact.target].append((fact, start))
            report.reviewed_fact_count += 1
        except (ValueError, ValidationError):
            report.issues.append(f"第 {index + 1} 项事实的目标、类型或原文引用未通过，未采纳该项。")
    for target, facts in grouped.items():
        values = list(dict.fromkeys(fact.value for fact, _ in facts))
        _, section, key = target.split(".")
        previous = getattr(getattr(alert.entities, section), key)
        if len(values) > 1:
            report.issues.append(f"{target} 出现多个不同值，首批单主体合并未采纳；保留原始证据。")
            continue
        selected: str | int | list[str] = values[0]
        if key == "ip_addresses":
            selected = list(dict.fromkeys([*(previous or []), values[0]]))
        elif key == "process_id":
            selected = int(values[0])
        if previous == selected:
            continue
        fact, start = facts[0]
        # A primary scalar correction must not silently invalidate an existing process tree.
        if section == "process" and alert.entities.process.observations and previous:
            report.issues.append(f"{target} 的纠正涉及已有进程观察，等待逐观察合并支持；未覆盖原值。")
            continue
        if section in {"process", "file"} and key.endswith("_path"):
            entity = getattr(alert.entities, section)
            if any(getattr(entity, key, None) for key in ("md5", "sha1", "sha256")):
                report.issues.append(f"{target} 涉及已有哈希归属，未自动把原哈希绑定到新路径。")
                continue
        report.changes.append(
            NormalizationFactChange(
                target=target,
                before=previous,
                after=selected,
                source_quote=fact.source_quote,
                source_start=start,
                source_end=start + len(fact.source_quote) if start is not None else None,
                reference_validation_status="verified" if request.reference_validation_enabled else "not_checked",
                reason=fact.reason,
                canonical_status="shadow" if report.mode == "shadow" else "applied",
            )
        )


def apply_normalization_changes(alert: AlertInput, request: NormalizationAssistRequest, report: NormalizationAssistResult) -> AlertInput:
    if report.mode != "apply" or report.status == "failed":
        return alert
    if not report.changes:
        return apply_observation_changes(alert, request, report)
    result = alert.model_copy(deep=True)
    provenance = result.extensions.setdefault("canonical_field_provenance", [])
    for change in report.changes:
        _, section, key = change.target.split(".")
        setattr(getattr(result.entities, section), key, change.after)
        provenance.append(
            CanonicalFieldProvenance(
                canonical_path=change.target,
                selected_value=str(change.after),
                selected_from=f"{request.source_path}#semantic[{change.source_start}:{change.source_end}]" if change.reference_validation_status == "verified" else f"{request.source_path}#semantic-unverified",
                source_layer=request.source_layer,
                trust_level=request.source_trust,
                selection_reason="llm_semantic_review: " + change.reason,
                alternative_values=[str(change.before)] if change.before is not None else [],
            ).model_dump(mode="json")
        )
    targets = {change.target for change in report.changes}
    for section, prefix in (("process", "process"), ("file", "file")):
        entity = getattr(result.entities, section)
        if f"entities.{section}.{prefix}_path" in targets:
            path = getattr(entity, f"{prefix}_path")
            name = (PureWindowsPath(path) if "\\" in path else PurePosixPath(path)).name
            setattr(entity, f"{prefix}_name", name)
    if "entities.file.file_path" in targets:
        file = result.entities.file
        if not any(item.file_path == file.file_path for item in file.observations):
            file.observations.append(
                FileObservationRef(
                    observation_id="FILE-SEM-" + stable_hash([request.source_path, file.file_path])[:12],
                    evidence_path=f"{request.source_path}#semantic",
                    relation=FileObservationRelation.OBSERVED_ARTIFACT,
                    file_path=file.file_path,
                    file_name=file.file_name,
                )
            )
    # Validate the whole contract after typed updates, never mutate the original snapshot.
    return AlertInput.model_validate(result.model_dump(mode="json"))
