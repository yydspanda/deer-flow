from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.gateway.routers import soc_memory
from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.core import SocMemoryLessonDraftService, SocMemoryService
from soc_agent.llm import JsonLLMMemoryLessonDrafter, LLMChatResponse
from soc_agent.memory import InMemoryMemoryCandidateRepository
from soc_agent.prompts.memory_lesson import (
    MEMORY_LESSON_DRAFT_PROMPT_VERSION,
    build_memory_lesson_draft_prompt,
)


class FakeChatClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse:
        self.calls.append({"messages": messages, "model_name": model_name})
        return LLMChatResponse(
            content=self.content,
            model_name="lesson-test-model",
            usage={"input_tokens": 321, "output_tokens": 123},
            metadata={
                "thinking_enabled_requested": False,
                "json_mode_requested": False,
                "provider_duration_ms": 12.5,
                "usage_measurement": {
                    "status": "reported",
                    "method": "provider_usage",
                },
            },
        )


class SequenceFakeChatClient(FakeChatClient):
    def __init__(self, contents: list[str]) -> None:
        super().__init__("")
        self.contents = list(contents)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse:
        self.content = self.contents.pop(0)
        return super().complete(messages, model_name=model_name)


def test_memory_lesson_prompt_uses_bounded_sources_and_tail_contract() -> None:
    candidate = _candidate()

    prompt = build_memory_lesson_draft_prompt(
        candidate,
        reviewer_verdict=Verdict.FALSE_POSITIVE,
        reviewer_context=("该流量实际访问平安内部 paic.com.cn/pws/askbob-gpt LLM 服务，并非真实反弹 Shell。"),
    )

    assert prompt.prompt_version == MEMORY_LESSON_DRAFT_PROMPT_VERSION
    assert prompt.context["machine_applicability"] == candidate.applicability.model_dump(mode="json")
    assert any(item.source_kind == "reviewer_context" and "askbob-gpt" in item.value for item in prompt.source_catalog)
    assert any(item.source_kind == "reviewer_verdict" and item.value == "false_positive" for item in prompt.source_catalog)
    assert "Do not output applicability_conditions" in prompt.system
    assert prompt.user.rstrip().endswith("</final_checklist>")
    assert '"schema_version": "soc.memory_business_lesson_model_output.v2"' in (prompt.user)
    assert '"additionalProperties": false' in prompt.user
    assert '"required": [' in prompt.user


def test_memory_lesson_drafter_builds_high_quality_askbob_draft_without_persistence() -> None:
    repository = InMemoryMemoryCandidateRepository()
    memory_service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
    )
    candidate = memory_service.propose_candidate(_candidate_command())
    prompt = build_memory_lesson_draft_prompt(
        candidate,
        reviewer_verdict=Verdict.FALSE_POSITIVE,
        reviewer_context=("该流量实际访问平安内部 paic.com.cn/pws/askbob-gpt LLM 服务，并非真实反弹 Shell。"),
    )
    verdict_ref = next(item.source_ref for item in prompt.source_catalog if item.source_kind == "reviewer_verdict")
    reviewer_ref = next(item.source_ref for item in prompt.source_catalog if item.source_kind == "reviewer_context")
    candidate_ref = next(item.source_ref for item in prompt.source_catalog if item.label == "candidate_content")
    client = FakeChatClient(
        json.dumps(
            {
                "schema_version": "soc.memory_business_lesson_model_output.v2",
                "reviewer_verdict": "false_positive",
                "conclusion": ("该流量实际访问平安内部 AskBob LLM 服务，并非真实反弹 Shell；仅在相同服务和行为模式命中时复用误报结论。"),
                "supporting_source_refs": [verdict_ref, reviewer_ref, candidate_ref],
                "business_rationale": [
                    {
                        "statement": ("运营专家确认 paic.com.cn/pws/askbob-gpt 是平安内部 LLM 服务。"),
                        "source_refs": [verdict_ref, reviewer_ref],
                    },
                    {
                        "statement": "重复样本具有一致行为模式和一致无风险结论。",
                        "source_refs": [candidate_ref],
                    },
                ],
                "generalization_boundaries": ["源和目的 IP 可以变化，但 AskBob 服务 URI 与行为指纹必须保持一致。"],
                "invalidation_conditions": ["服务 URI 或行为指纹变化，或当前告警出现新的命令执行和影响证据时失效。"],
                "handling_guidance": ["先校验全部机器适用条件；精确命中且无反证时复用误报结论，否则重新研判。"],
                "uncertainties": [],
            },
            ensure_ascii=False,
        )
    )
    draft_service = SocMemoryLessonDraftService(
        candidate_repository=repository,
        drafter=JsonLLMMemoryLessonDrafter(
            client=client,
            model_name="lesson-test-model",
        ),
    )

    draft = draft_service.draft_business_lesson(
        candidate.candidate_id,
        reviewer_verdict=Verdict.FALSE_POSITIVE,
        reviewer_context=("该流量实际访问平安内部 paic.com.cn/pws/askbob-gpt LLM 服务，并非真实反弹 Shell。"),
        promoted_facet_keys=["service_uri"],
        context=_review_context(),
    )

    assert "AskBob LLM 服务" in draft.lesson.conclusion
    assert draft.lesson.applicability_conditions == [
        "Required canonical facet behavior_fingerprint: reverse-shell-askbob",
        "Required canonical facet detection_key: pingan:ndr:reverse-shell",
        "Required canonical facet environment: prd",
        "Required canonical facet service_uri: paic.com.cn/pws/askbob-gpt",
    ]
    assert draft.lesson.invalidation_conditions[:2] == [
        "任一必需 canonical facet 与当前告警不匹配时，该经验失效。",
        "当前告警出现与已审核业务结论冲突的新证据或攻击影响时，必须重新研判。",
    ]
    assert draft.reviewer_verdict is Verdict.FALSE_POSITIVE
    assert draft.supporting_source_refs == [verdict_ref, reviewer_ref, candidate_ref]
    assert [item.model_dump(mode="json") for item in draft.rationale_sources] == [
        {
            "schema_version": "soc.memory_business_lesson_draft_rationale.v1",
            "statement": "运营专家确认 paic.com.cn/pws/askbob-gpt 是平安内部 LLM 服务。",
            "source_refs": [verdict_ref, reviewer_ref],
        },
        {
            "schema_version": "soc.memory_business_lesson_draft_rationale.v1",
            "statement": "重复样本具有一致行为模式和一致无风险结论。",
            "source_refs": [candidate_ref],
        },
    ]
    assert draft.decision_impact == "none"
    assert draft.review_required is True
    assert draft.persistence_performed is False
    assert draft.provenance.usage == {
        "input_tokens": 321,
        "output_tokens": 123,
        "total_tokens": 444,
    }
    assert repository.list_memory_records() == []
    assert repository.get_memory_candidate(candidate.candidate_id) == candidate

    reviewed = memory_service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="运营专家审核并确认 AskBob 内部服务经验。",
            record_lesson=draft.lesson,
            apply_to_future_matches=True,
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            clear_review_on_match=True,
        ),
        context=_review_context(),
    )
    assert reviewed.memory_record is not None
    assert reviewed.memory_record.business_lesson == draft.lesson


def test_memory_lesson_drafter_rejects_unknown_source_alias() -> None:
    candidate = _candidate()
    client = FakeChatClient(
        json.dumps(
            {
                "schema_version": "soc.memory_business_lesson_model_output.v2",
                "reviewer_verdict": "false_positive",
                "conclusion": "该结论引用了当前候选中不存在的来源，因此必须拒绝。",
                "supporting_source_refs": ["D-999"],
                "business_rationale": [{"statement": "这是无法解析的伪造来源。", "source_refs": ["D-999"]}],
                "generalization_boundaries": ["仅用于验证未知引用拒绝边界。"],
                "invalidation_conditions": ["任何真实运行都不得接受该未知引用。"],
                "handling_guidance": ["拒绝草稿并要求重新生成。"],
                "uncertainties": [],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(ValueError, match="unresolved source refs: D-999"):
        JsonLLMMemoryLessonDrafter(
            client=client,
            model_name="lesson-test-model",
        ).draft(candidate, reviewer_verdict=Verdict.FALSE_POSITIVE)


def test_memory_lesson_drafter_drops_only_empty_unknown_top_level_field() -> None:
    candidate = _candidate()
    client = FakeChatClient(
        json.dumps(
            {
                "schema_version": "soc.memory_business_lesson_model_output.v2",
                "reviewer_verdict": "false_positive",
                "conclusion": "该重复模式仅在审核后的精确范围内可复用无风险结论。",
                "supporting_source_refs": ["D-001"],
                "business_rationale": [
                    {
                        "statement": "候选摘要记录了重复样本的一致结论。",
                        "source_refs": ["D-001"],
                    }
                ],
                "generalization_boundaries": ["非必需实体可以变化，机器适用条件不变。"],
                "invalidation_conditions": [],
                "handling_guidance": ["先校验机器适用条件，不满足时重新研判。"],
                "uncertainties": [],
                "generalization_boundaries_text": None,
            },
            ensure_ascii=False,
        )
    )

    draft = JsonLLMMemoryLessonDrafter(
        client=client,
        model_name="lesson-test-model",
    ).draft(candidate, reviewer_verdict=Verdict.FALSE_POSITIVE)

    assert draft.provenance.repair_applied is True
    assert draft.provenance.repair_actions == ["drop_empty_unknown_field:generalization_boundaries_text"]
    assert draft.lesson.invalidation_conditions == [
        "任一必需 canonical facet 与当前告警不匹配时，该经验失效。",
        "当前告警出现与已审核业务结论冲突的新证据或攻击影响时，必须重新研判。",
    ]


def test_memory_lesson_drafter_canonicalizes_supporting_refs_to_rationale_union() -> None:
    candidate = _candidate()
    prompt = build_memory_lesson_draft_prompt(
        candidate,
        reviewer_verdict=Verdict.FALSE_POSITIVE,
    )
    verdict_ref = next(item.source_ref for item in prompt.source_catalog if item.source_kind == "reviewer_verdict")
    candidate_ref = next(item.source_ref for item in prompt.source_catalog if item.label == "candidate_content")
    client = FakeChatClient(
        json.dumps(
            {
                "schema_version": "soc.memory_business_lesson_model_output.v2",
                "reviewer_verdict": "false_positive",
                "conclusion": "该重复模式经审核为无风险业务行为，仅在精确适用条件命中时复用误报结论。",
                "supporting_source_refs": [f"D-{index:03d}" for index in range(1, 50)],
                "business_rationale": [
                    {
                        "statement": "审核人已明确选择无风险误报结论，候选记录提供了重复模式上下文。",
                        "source_refs": [verdict_ref, candidate_ref],
                    }
                ],
                "generalization_boundaries": ["非必需实体可以变化，机器适用条件必须保持一致。"],
                "invalidation_conditions": [],
                "handling_guidance": ["先校验机器适用条件，精确命中且无反证时复用审核结论。"],
                "uncertainties": ["审核人尚未提供更具体的业务解释。"],
            },
            ensure_ascii=False,
        )
    )

    draft = JsonLLMMemoryLessonDrafter(
        client=client,
        model_name="lesson-test-model",
    ).draft(candidate, reviewer_verdict=Verdict.FALSE_POSITIVE)

    assert len(client.calls) == 1
    assert draft.supporting_source_refs == [verdict_ref, candidate_ref]
    assert draft.provenance.repair_actions == ["normalize_supporting_refs_to_rationale_union"]
    assert draft.provenance.output_repair_call_count == 0


def test_memory_lesson_drafter_rejects_nonempty_unknown_top_level_field() -> None:
    candidate = _candidate()
    client = FakeChatClient(
        json.dumps(
            {
                "schema_version": "soc.memory_business_lesson_model_output.v2",
                "reviewer_verdict": "false_positive",
                "conclusion": "该重复模式仅在审核后的精确范围内可复用无风险结论。",
                "supporting_source_refs": ["D-001"],
                "business_rationale": [
                    {
                        "statement": "候选摘要记录了重复样本的一致结论。",
                        "source_refs": ["D-001"],
                    }
                ],
                "generalization_boundaries": ["非必需实体可以变化，机器适用条件不变。"],
                "invalidation_conditions": ["必需条件不匹配或当前出现反证时失效。"],
                "handling_guidance": ["先校验机器适用条件，不满足时重新研判。"],
                "uncertainties": [],
                "unreviewed_authority": "auto_close",
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(ValueError, match="unreviewed_authority"):
        JsonLLMMemoryLessonDrafter(
            client=client,
            model_name="lesson-test-model",
        ).draft(candidate, reviewer_verdict=Verdict.FALSE_POSITIVE)


def test_memory_lesson_drafter_repairs_one_incomplete_provider_output() -> None:
    candidate = _candidate()
    client = SequenceFakeChatClient(
        [
            json.dumps(
                {
                    "conclusion": "该重复模式仅在审核后的精确范围内可复用无风险结论。",
                    "business_rationale": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "schema_version": "soc.memory_business_lesson_model_output.v2",
                    "reviewer_verdict": "false_positive",
                    "conclusion": "该重复模式仅在审核后的精确范围内可复用无风险结论。",
                    "supporting_source_refs": ["D-001"],
                    "business_rationale": [
                        {
                            "statement": "候选摘要记录了重复样本的一致结论。",
                            "source_refs": ["D-001"],
                        }
                    ],
                    "generalization_boundaries": ["非必需实体可以变化，机器适用条件不变。"],
                    "invalidation_conditions": [],
                    "handling_guidance": ["先校验机器适用条件，不满足时重新研判。"],
                    "uncertainties": [],
                },
                ensure_ascii=False,
            ),
        ]
    )

    draft = JsonLLMMemoryLessonDrafter(
        client=client,
        model_name="lesson-test-model",
    ).draft(candidate, reviewer_verdict=Verdict.FALSE_POSITIVE)

    assert len(client.calls) == 2
    assert draft.provenance.provider_call_count == 2
    assert draft.provenance.output_repair_call_count == 1
    assert draft.provenance.repair_actions == ["provider_output_repair"]
    assert draft.provenance.repair_prompt_hash is not None
    assert draft.provenance.usage == {
        "input_tokens": 642,
        "output_tokens": 246,
        "total_tokens": 888,
    }


@pytest.mark.parametrize(
    ("conclusion", "expected_error"),
    [
        (
            "该重复模式访问平安内部 paic.com.cn/pya/askbob-gpt 服务，并非真实反弹 Shell。",
            "literal identifiers absent from source catalog",
        ),
        (
            "该重复模式是已确认的无风险业务行为，命中后不做阻断。",
            "action language reserved for handling_guidance",
        ),
    ],
)
def test_memory_lesson_drafter_rejects_ungrounded_identifier_or_action_conclusion(
    conclusion: str,
    expected_error: str,
) -> None:
    client = FakeChatClient(
        json.dumps(
            {
                "schema_version": "soc.memory_business_lesson_model_output.v2",
                "reviewer_verdict": "false_positive",
                "conclusion": conclusion,
                "supporting_source_refs": ["D-001"],
                "business_rationale": [
                    {
                        "statement": "候选摘要记录了重复样本的一致结论。",
                        "source_refs": ["D-001"],
                    }
                ],
                "generalization_boundaries": ["非必需实体可以变化，机器适用条件不变。"],
                "invalidation_conditions": [],
                "handling_guidance": ["先校验机器适用条件，不满足时重新研判。"],
                "uncertainties": [],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(ValueError, match=expected_error):
        JsonLLMMemoryLessonDrafter(
            client=client,
            model_name="lesson-test-model",
            output_retry_attempts=0,
        ).draft(_candidate(), reviewer_verdict=Verdict.FALSE_POSITIVE)


def test_memory_lesson_api_returns_review_only_draft() -> None:
    repository = InMemoryMemoryCandidateRepository()
    candidate = SocMemoryService(candidate_repository=repository).propose_candidate(_candidate_command())
    client = FakeChatClient(
        json.dumps(
            {
                "schema_version": "soc.memory_business_lesson_model_output.v2",
                "reviewer_verdict": "false_positive",
                "conclusion": "重复样本呈现一致无风险模式，可在精确适用条件命中且无反证时复用。",
                "supporting_source_refs": ["D-001", "D-002"],
                "business_rationale": [
                    {
                        "statement": "候选摘要和代表结论均显示重复样本具有一致无风险结果。",
                        "source_refs": ["D-001", "D-002"],
                    }
                ],
                "generalization_boundaries": ["非必需实体值可以变化，但行为指纹和检测锚点必须保持一致。"],
                "invalidation_conditions": ["强锚点不匹配或当前证据出现新的攻击影响时失效。"],
                "handling_guidance": ["机器适用范围全部命中后再由专家确认是否启用该经验。"],
                "uncertainties": ["内部服务归属仍需专家在确认前核对。"],
            },
            ensure_ascii=False,
        )
    )
    service = SocMemoryLessonDraftService(
        candidate_repository=repository,
        drafter=JsonLLMMemoryLessonDrafter(
            client=client,
            model_name="lesson-test-model",
        ),
    )
    request = SimpleNamespace(
        headers={
            "x-soc-surface": "web",
            "x-request-id": "REQ-LESSON-API",
            "x-trace-id": "TRACE-LESSON-API",
        },
        state=SimpleNamespace(
            auth_source="session",
            user=SimpleNamespace(id="lesson-api-reviewer", system_role="user"),
        ),
    )

    draft = soc_memory.draft_memory_business_lesson(
        candidate.candidate_id,
        soc_memory.MemoryBusinessLessonDraftRequest(
            reviewer_verdict=Verdict.FALSE_POSITIVE,
        ),
        request=request,
        service=service,
    )

    assert draft.candidate_id == candidate.candidate_id
    assert draft.review_required is True
    assert draft.persistence_performed is False
    assert draft.uncertainties == ["内部服务归属仍需专家在确认前核对。"]
    assert repository.list_memory_records() == []


def _candidate():
    repository = InMemoryMemoryCandidateRepository()
    return SocMemoryService(candidate_repository=repository).propose_candidate(_candidate_command())


def _candidate_command() -> SocMemoryCandidateCreateCommand:
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.BENIGN_PATTERN,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="[无风险经验候选] Reverse connection detector",
        content=("5 条重复样本命中反连检测，最终均判定为无风险；代表样本访问 paic.com.cn/pws/askbob-gpt。"),
        tenant_scope="pingan",
        tenant_id="pingan",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.REPEATED_PATTERN,
            source_id="memory_pattern:askbob",
            run_id="RUN-ASKBOB-1",
            alert_id="2025642",
        ),
        evidence_refs=["E-ASKBOB-URI", "R-ASKBOB-OUTCOME"],
        validity=SocMemoryCandidateValidity(
            valid_from=datetime(2026, 8, 16, tzinfo=UTC),
            notes="Repeated AskBob pattern candidate requires expert review.",
        ),
        idempotency_key="memory:test:askbob-business-lesson",
        confidence=1.0,
        facets={
            "environment": ["prd"],
            "detection_key": ["pingan:ndr:reverse-shell"],
            "behavior_fingerprint": ["reverse-shell-askbob"],
            "service_uri": ["paic.com.cn/pws/askbob-gpt"],
        },
        applicability=SocMemoryApplicabilitySpec(
            profile_id="soc.pingan",
            profile_version="2",
            feature_schema_version="soc.memory_features.pingan.v2",
            required_facets={
                "environment": ["prd"],
                "detection_key": ["pingan:ndr:reverse-shell"],
                "behavior_fingerprint": ["reverse-shell-askbob"],
            },
            optional_facets={
                "service_uri": ["paic.com.cn/pws/askbob-gpt"],
            },
        ),
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        review_owner="soc_memory_reviewer",
        labels=["repeated-pattern", "quality-gated", "candidate-only"],
        metadata={
            "cohort_quality": {
                "support_count": 5,
                "distinct_source_count": 5,
                "conclusive_count": 5,
                "unresolved_count": 0,
                "verdict_counts": {"false_positive": 5},
                "dominant_risk_class": "benign",
                "consistency_ratio": 1.0,
            }
        },
    )


def _review_context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-memory-reviewer-test",
            actor_type=ActorType.USER,
            surface=EntrySurface.WEB,
            roles=["soc_analyst", "soc_memory_reviewer"],
            auth_source=ActorAuthSource.SESSION,
        )
    )
