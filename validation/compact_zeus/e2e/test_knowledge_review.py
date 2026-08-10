from __future__ import annotations

from validation.compact_zeus.e2e.knowledge_review import (
    compile_case_knowledge_review,
    compile_knowledge_review_package,
    render_knowledge_review_markdown,
)


def _analysis(*, statement: str, destination: str, scope: str) -> dict:
    return {
        "evidence": [
            {
                "evidence_ref": "E-A1B2C3D4E5F6",
                "source": "detection.rule_name",
                "description": "规则名称",
                "value": "Suspicious-Remote-Service",
            }
        ],
        "reasoning": [
            {
                "reasoning_id": "R-01",
                "statement": "远程服务行为需要结合授权上下文判断。",
                "basis": ["current_evidence", "general_security_knowledge"],
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "context_refs": [],
                "confidence": 0.8,
            }
        ],
        "knowledge_candidates": [
            {
                "candidate_id": "K-01",
                "statement": statement,
                "destination_hint": destination,
                "scope_hint": scope,
                "evidence_refs": ["E-A1B2C3D4E5F6"],
                "reasoning_refs": ["R-01"],
                "rationale": "该方法可能在后续同类告警中复用。",
            }
        ],
    }


def _grounding() -> dict:
    return {
        "items": [{"evidence_ref": "E-A1B2C3D4E5F6", "status": "grounded"}],
        "reasoning_items": [{"reasoning_id": "R-01", "status": "grounded"}],
    }


def _case(*, alert_id: str, statement: str, destination: str, scope: str) -> dict:
    return compile_case_knowledge_review(
        alert_id=alert_id,
        run_id=f"run-{alert_id}",
        source={"source_type": "edr", "topic": "ptp-edr"},
        analysis=_analysis(
            statement=statement,
            destination=destination,
            scope=scope,
        ),
        grounding=_grounding(),
    )


def test_case_review_keeps_candidate_inert_and_links_grounded_support() -> None:
    review = _case(
        alert_id="1",
        statement="远程服务告警应同时核对发起进程与授权变更记录。",
        destination="general_skill",
        scope="global",
    )

    assert review["candidate_count"] == 1
    assert review["grounded_candidate_count"] == 1
    candidate = review["candidates"][0]
    assert candidate["recommended_destination"] == "general_skill"
    assert candidate["review_status"] == "pending_review"
    assert candidate["memory_write_performed"] is False
    assert candidate["evidence_support"][0]["grounding_status"] == "grounded"
    assert candidate["reasoning_support"][0]["reasoning"]["reasoning_id"] == "R-01"


def test_review_package_deduplicates_only_exact_normalized_statements() -> None:
    first = _case(
        alert_id="1",
        statement="远程服务告警应核对授权记录。",
        destination="general_skill",
        scope="global",
    )
    second = _case(
        alert_id="2",
        statement="  远程服务告警应核对授权记录。 ",
        destination="general_skill",
        scope="global",
    )

    package = compile_knowledge_review_package([first, second])

    assert package["summary"]["raw_candidate_count"] == 2
    assert package["summary"]["review_candidate_count"] == 1
    assert package["candidates"][0]["distinct_alert_count"] == 2
    assert package["candidates"][0]["review"]["status"] == "pending_review"


def test_review_destination_separates_governed_fact_and_event_ioc() -> None:
    governed = _case(
        alert_id="1",
        statement="护网红队来源需要通过有时效的授权测试事实确认。",
        destination="tenant_memory",
        scope="tenant",
    )
    event_ioc = _case(
        alert_id="2",
        statement="30.1.2.3 是本次攻击源。",
        destination="tenant_memory",
        scope="event",
    )

    assert governed["candidates"][0]["recommended_destination"] == "governed_context"
    assert event_ioc["candidates"][0]["recommended_destination"] == "reject_or_verify"

    markdown = render_knowledge_review_markdown(
        compile_knowledge_review_package([governed, event_ioc])
    )
    assert "pending_review" in markdown
    assert "候选不是事实" in markdown
