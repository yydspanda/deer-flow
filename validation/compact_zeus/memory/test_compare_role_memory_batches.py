from __future__ import annotations

from validation.compact_zeus.memory.compare_role_memory_batches import (
    _collect_memory_reference_paths,
    compare_batches,
    render_markdown,
)


def _manifest(*, persisted: bool) -> dict:
    return {
        "status": "completed",
        "source": {"sha256": "a" * 64},
        "execution": {
            "model_name": "flash",
            "thinking_enabled_requested": False,
            "role_verifier_enabled": True,
            "role_verifier_model_name": "pro",
            "default_tenant_id": "tenant-a",
            "persist": persisted,
        },
    }


def _item(*, memory: bool) -> dict:
    context_catalog = []
    context_refs = []
    if memory:
        context_catalog.append(
            {
                "context_ref": "M-ONE",
                "kind": "confirmed_memory",
                "metadata": {"memory_id": "MEM-ONE"},
            }
        )
        context_refs.append("M-ONE")
    return {
        "execution": {
            "end_to_end_total_duration_ms": 1000 if memory else 900,
        },
        "summary": {
            "source_type": "ndr",
            "usage": {
                "input_tokens": 120 if memory else 100,
                "output_tokens": 20,
                "total_tokens": 140 if memory else 120,
            },
        },
        "analysis_run": {
            "run_id": "RUN-MEM" if memory else "RUN-BASE",
            "alert_id": "A-1",
            "input_hash": "same-hash",
            "status": "success",
            "llm_analysis_request": {"context_catalog": context_catalog},
            "analysis": {
                "verdict": "suspicious",
                "confidence": 0.8,
                "summary": "summary",
                "reason": "reason",
                "reasoning": [{"reasoning_id": "R-00", "context_refs": context_refs}],
                "network_direction": {
                    "observed_flow": "source_to_destination",
                    "boundary_direction": "internal_to_internal",
                },
                "role_adjudication": {"roles": []},
            },
            "analysis_output_quality": {
                "status": "accepted",
                "repair_attempted": False,
                "deterministic_fallback_used": False,
            },
            "decision": {
                "needs_review": False,
                "review_reasons": [],
                "evidence_state": "sufficient",
            },
            "role_verification_trigger": {
                "triggered": False,
                "claim_count": 0,
                "reasons": [],
            },
        },
    }


def test_collect_memory_reference_paths_retains_exact_locations() -> None:
    result = _collect_memory_reference_paths(
        {"reasoning": [{"context_refs": ["M-ONE", "S-ONE"]}]}
    )

    assert result == {
        "M-ONE": ["reasoning[0].context_refs[0]"],
    }


def test_compare_reports_own_memory_selection_and_explicit_citation() -> None:
    report = compare_batches(
        baseline_manifest=_manifest(persisted=False),
        baseline_items={"A-1": _item(memory=False)},
        current_manifest=_manifest(persisted=True),
        current_items={"A-1": _item(memory=True)},
        seed_report={
            "decision_directive_count": 0,
            "items": [{"memory_id": "MEM-ONE", "alert_id": "A-1"}],
        },
        retrievals={
            "A-1": {
                "policy_version": "soc.memory_retrieval_policy.v2",
                "total_candidate_count": 1,
                "returned_count": 1,
                "total_token_estimate": 50,
                "skipped_missing_strong_anchor": 0,
                "skipped_below_min_score": 0,
                "matches": [
                    {
                        "memory_id": "MEM-ONE",
                        "memory_version": 2,
                        "score": 40.0,
                        "match_reasons": ["facet:rule_code=r1"],
                        "matched_facets": {"rule_code": ["r1"]},
                        "anchor_match_reasons": ["anchor:rule_code=r1"],
                        "matched_anchor_facets": {"rule_code": ["r1"]},
                        "token_estimate": 50,
                    }
                ],
            }
        },
        baseline_path="baseline",
        current_path="current",
    )

    summary = report["summary"]
    assert summary["same_input_hash_count"] == 1
    assert summary["own_memory_selected_count"] == 1
    assert summary["own_memory_rank_one_count"] == 1
    assert summary["model_cited_any_memory_count"] == 1
    assert summary["model_cited_own_memory_count"] == 1
    assert summary["model_core_cited_any_memory_count"] == 1
    assert summary["model_core_cited_own_memory_count"] == 1
    assert summary["memory_projection_mismatch_alert_ids"] == []
    assert report["cases"][0]["memory_selection"]["selected"][0]["citation_paths"] == [
        "analysis.reasoning[0].context_refs[0]"
    ]
    assert "MEM-ONE" in render_markdown(report)
