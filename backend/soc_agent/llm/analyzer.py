"""LLM-backed SOC analysis node adapters.

This module does not choose runtime control flow. It only implements the
bounded analysis node contract: build the versioned prompt, call a supplied
chat client, parse/repair JSON, and return an auditable node output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from soc_agent.contracts import AnalysisNodeOutput, LLMAnalysisRequest
from soc_agent.llm.json_parser import parse_analysis_result_output
from soc_agent.pipeline.analyzer import StubLLMAnalyzer
from soc_agent.prompts import ANALYSIS_PROMPT_VERSION, build_analysis_prompt
from soc_agent.utils.hashing import stable_hash

LLM_ANALYZER_STEP_NAME = "analyze_llm"


@dataclass(frozen=True)
class LLMChatResponse:
    """Normalized response shape returned by SOC LLM chat clients."""

    content: Any
    model_name: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class LLMChatClient(Protocol):
    """Small adapter protocol for DeerFlow/OpenAI/local model clients."""

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse | str: ...


class JsonLLMAnalyzer:
    """Prompt + JSON parser backed SOC analysis node.

    The caller must explicitly inject this analyzer into the runtime. Default
    SOC runtime construction still uses ``StubLLMAnalyzer``.
    """

    step_name = LLM_ANALYZER_STEP_NAME

    def __init__(self, *, client: LLMChatClient, model_name: str) -> None:
        if not model_name:
            raise ValueError("model_name is required for JsonLLMAnalyzer")
        self._client = client
        self.model_name = model_name
        self.prompt_version = ANALYSIS_PROMPT_VERSION

    def analyze(self, request: LLMAnalysisRequest) -> AnalysisNodeOutput:
        prompt = build_analysis_prompt(request)
        response = _coerce_chat_response(self._client.complete(prompt.messages(), model_name=self.model_name))
        parsed = parse_analysis_result_output(response.content)

        metadata: dict[str, Any] = {
            "analyzer": "json_llm",
            "repair_applied": parsed.repair_applied,
            "prompt_hash": stable_hash({"messages": prompt.messages()}),
            "candidate_hash": stable_hash({"candidate_text": parsed.candidate_text}),
        }
        if parsed.repair_log:
            metadata["repair_log"] = parsed.repair_log
        if response.usage:
            metadata["usage"] = dict(response.usage)
        if response.metadata:
            metadata["response_metadata"] = dict(response.metadata)

        return AnalysisNodeOutput(
            analysis=parsed.result,
            model_name=response.model_name or self.model_name,
            prompt_version=prompt.prompt_version,
            parser_version=parsed.parser_version,
            metadata=metadata,
        )


def build_optional_llm_analyzer(
    *,
    enabled: bool,
    client: LLMChatClient | None = None,
    model_name: str = "stub",
) -> StubLLMAnalyzer | JsonLLMAnalyzer:
    """Feature-flagged analyzer factory.

    ``enabled=False`` is the safe default and returns the deterministic stub.
    ``enabled=True`` requires an injected client so tests and future entry
    adapters can choose the model provider explicitly.
    """

    if not enabled:
        return StubLLMAnalyzer()
    if client is None:
        raise ValueError("client is required when SOC LLM analyzer is enabled")
    return JsonLLMAnalyzer(client=client, model_name=model_name)


def _coerce_chat_response(response: LLMChatResponse | str) -> LLMChatResponse:
    if isinstance(response, LLMChatResponse):
        return response
    return LLMChatResponse(content=response)
