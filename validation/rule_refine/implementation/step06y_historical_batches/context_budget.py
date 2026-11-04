"""Operator-assumed model capacity and a dependency-free token estimate.

This estimator counts UTF-8 content bytes plus a chat-envelope allowance.
It usually overestimates BPE token usage, but is neither an exact tokenizer nor
a verified bound for this internal provider. Actual API usage remains separate.
"""

import hashlib
from dataclasses import dataclass

from pydantic import Field, create_model

TOKEN_ESTIMATION_BASIS = "utf8_message_content_bytes_plus_chat_allowance_v1"
STRUCTURAL_INPUT_BYTES = 32 * 1024 * 1024
STRUCTURAL_RESPONSE_BYTES = 40 * 1024 * 1024
MESSAGE_ALLOWANCE = 64
CHAT_ALLOWANCE = 256
MISSING_CERTAINTY = "未提供（模型未返回判断把握说明）"


@dataclass(frozen=True)
class ContextBudget:
    assumed_context_tokens: int = 1_000_000
    input_fraction: float = 0.8
    timeout_seconds: int = 600
    retry_attempts: int = 3
    retry_delay_seconds: int = 60

    def __post_init__(self):
        for key in (
            "assumed_context_tokens",
            "timeout_seconds",
            "retry_attempts",
            "retry_delay_seconds",
        ):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError("invalid_context_budget:" + key)
        if (
            isinstance(self.input_fraction, bool)
            or not isinstance(self.input_fraction, (int, float))
            or not 0 < self.input_fraction < 1
            or self.input_token_budget < 1
            or self.timeout_seconds > 600
        ):
            raise ValueError("invalid_context_budget_allocation")

    @property
    def input_token_budget(self):
        return int(self.assumed_context_tokens * self.input_fraction)

    @property
    def input_bytes(self):
        """JSON construction safeguard, not the model-input dispatch budget."""
        return STRUCTURAL_INPUT_BYTES

    @property
    def output_bytes(self):
        """Local record safeguard; never sent as an output generation quota."""
        return STRUCTURAL_RESPONSE_BYTES

    @classmethod
    def from_limits(cls, existing):
        return cls(
            retry_attempts=existing.retry_attempts,
            retry_delay_seconds=existing.retry_delay_seconds,
        )

    def audit(self):
        return {
            "assumed_context_tokens": self.assumed_context_tokens,
            "input_fraction": self.input_fraction,
            "input_token_budget": self.input_token_budget,
            "model_output_token_limit": None,
            "context_capacity_verified": False,
            "context_capacity_basis": "operator_assumption",
            "token_estimation_basis": TOKEN_ESTIMATION_BASIS,
            "token_count_exact": False,
            "token_estimate_is_verified_upper_bound": False,
            "structural_max_input_bytes": self.input_bytes,
            "structural_max_response_bytes": self.output_bytes,
        }


def from_limits(existing):
    return ContextBudget.from_limits(existing)


def estimate_prompt_tokens(messages):
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages_required")
    total = CHAT_ALLOWANCE
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in {"system", "user", "assistant"}
            or not isinstance(message["content"], str)
        ):
            raise ValueError("unsupported_message_for_token_estimation")
        total += len(message["content"].encode("utf-8")) + MESSAGE_ALLOWANCE
    return total


def _evaluate_all(definition, samples, *, original, evaluation):
    """Chunk only local CPU evaluation; the model receives all planned samples."""
    template = original(definition, [])
    if not isinstance(samples, list):
        raise ValueError("样本必须为列表")
    seen, empty = set(), []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("样本必须为对象")
        identity = sample.get("alert_id")
        if (
            not isinstance(identity, str)
            or not 1 <= len(identity) <= 512
            or identity != identity.strip()
        ):
            raise ValueError("告警 ID 必须为 1～512 字符且无首尾空白")
        if identity in seen:
            raise ValueError("告警 ID 不能重复")
        seen.add(identity)
        logs = sample.get("raw_logs")
        if not isinstance(logs, list) or any(not isinstance(log, dict) for log in logs):
            raise ValueError("每个样本需要日志对象的列表")
        if not logs:
            empty.append(identity)
    parsed = evaluation.FilterDefinition.model_validate(definition)
    raw = evaluation._encode(
        {"filter": parsed.model_dump(mode="json"), "samples": samples},
        evaluation.RECORD_BYTES,
    )
    counts = {"matched": 0, "not_matched": 0, "unknown": 0}
    results = []
    for sample in samples:
        for offset in range(0, len(sample["raw_logs"]), 100):
            chunk = original(
                definition,
                [
                    {
                        "alert_id": sample["alert_id"],
                        "raw_logs": sample["raw_logs"][offset : offset + 100],
                    }
                ],
            )
            for key in counts:
                counts[key] += chunk["counts"][key]
            results.extend(
                {**row, "log_index": row["log_index"] + offset}
                for row in chunk["results"]
            )
    return {
        **template,
        "status": "evaluated" if results else "insufficient_evidence",
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "input_alert_count": len(samples),
        "input_log_count": len(results),
        "counts": counts,
        "empty_alert_ids": empty,
        "results": results,
    }


def install_compatibility(loaded):
    """Adapt only modules privately loaded by this standalone historical tool."""
    analysis, evaluation = loaded.analysis, loaded.filter_evaluation
    if getattr(analysis, "_historical_context_compatibility", False):
        return
    analysis.AnalysisDraft = create_model(
        "HistoricalContextAnalysisDraft",
        __base__=analysis.AnalysisDraft,
        sample_alert_ids=(list[analysis.Text], Field(default_factory=list)),
        certainty=(analysis.Description, MISSING_CERTAINTY),
    )
    original = evaluation.evaluate_filter

    def evaluate_filter(definition, samples):
        return _evaluate_all(
            definition, samples, original=original, evaluation=evaluation
        )

    evaluation.evaluate_filter = evaluate_filter
    analysis._historical_context_compatibility = True
