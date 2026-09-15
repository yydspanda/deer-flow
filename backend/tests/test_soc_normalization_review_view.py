"""Read-only DEV presentation, with synthetic saved review records."""

import pytest

from soc_agent.contracts import AnalysisRun, NormalizationAssistRequest, NormalizationAssistResult, NormalizationObservationChange
from soc_agent.demo.normalization_review import build_normalization_review_view


def _run(mode="shadow", status="shadow"):
    return AnalysisRun(
        run_id="RUN-semantic-view",
        alert_id="synthetic",
        status="success",
        input_hash="fixture",
        pipeline_version="test",
        model_name="fixture",
        prompt_version="fixture",
        normalization_assist_request=NormalizationAssistRequest(alert_id="synthetic", source={}, detection={}, configuration_hash="fixture"),
        normalization_assistance=NormalizationAssistResult(
            status=status,
            mode=mode,
            request_hash="fixture",
            model_name="fixture",
            prompt_version="soc-normalization-review-v2",
            observation_changes=[
                NormalizationObservationChange(
                    target="entities.file.observations[0]",
                    before=None,
                    after={"observation_id": "file-fixture", "file_name": "yak.exe", "file_path": r"D:\tools\yak.exe"},
                    source_id="L0",
                    source_path="message",
                    source_quote='file="yak.exe"',
                    source_start=0,
                    source_end=14,
                    reason="日志明确报告检测文件。",
                    canonical_status="shadow" if mode == "shadow" else "applied",
                )
            ],
            metadata={"usage": {"total_tokens": 123}},
        ),
    )


def test_shadow_view_is_explicit_and_does_not_mutate_saved_run():
    run = _run()
    before = run.model_dump_json()
    view = build_normalization_review_view(run)
    assert view.effect_label == "仅对比，未用于本次研判或经验匹配"
    assert view.after_label == "模型建议"
    assert view.changes[0].fields[0].label == "文件名"
    assert view.changes[0].fields[0].before is None
    assert view.changes[0].source_quote == 'file="yak.exe"'
    assert view.total_tokens == 123
    assert run.model_dump_json() == before


@pytest.mark.parametrize(
    "mode,status,expected",
    [
        ("apply", "applied", "核对完成，已采用 1 项补充；供后续研判与经验条件构建使用"),
        ("apply", "failed", "核对失败，沿用 Adapter 结果"),
        ("shadow", "skipped", "本次未执行语义核对"),
    ],
)
def test_view_does_not_claim_failed_or_skipped_results_applied(mode, status, expected):
    run = _run(mode, status)
    if status in {"failed", "skipped"}:
        run.normalization_assistance.observation_changes = []
    view = build_normalization_review_view(run)
    assert view.effect_label == expected


def test_legacy_run_has_no_fabricated_semantic_review():
    from soc_agent.demo.corpus_workbench import _execution_view

    run = _run()
    run.normalization_assistance = None
    assert build_normalization_review_view(run) is None
    run.normalization_assist_request = None
    assert not any(phase.phase == "semantic_review" for phase in _execution_view(alert_id=run.alert_id, run=run, observation=None, replay=None, candidate=None).phases)


def test_direct_policy_preserves_completed_semantic_review():
    from test_soc_direct_resolution import policy_service
    from test_soc_tenant_policy import _run as policy_sample

    from soc_agent.contracts import PipelineStepTrace
    from soc_agent.core.direct_resolution import SocDirectResolutionService
    from soc_agent.core.runtime import analyze_alert
    from soc_agent.demo.corpus_workbench import _audit_bundle, _execution_view

    run = analyze_alert(policy_sample(rule_code="RPAADM_000558").input_payload, direct_resolution=SocDirectResolutionService(tenant_policy_service=policy_service(), environment="dev"))
    saved = _run("apply", "applied")
    run.normalization_assist_request = saved.normalization_assist_request
    run.normalization_assistance = saved.normalization_assistance
    run.steps.append(PipelineStepTrace(step_name="normalization_assist", status="success", started_at=run.started_at, ended_at=run.ended_at, duration_ms=10))
    execution = _execution_view(alert_id=run.alert_id, run=run, observation=None, replay=None, candidate=None)
    semantic = next(phase for phase in execution.phases if phase.phase == "semantic_review")
    assert semantic.status == "success"
    assert "已采用 1 项补充" in semantic.summary
    audit = _audit_bundle(run=run, execution=execution, observation=None, replay=None, candidates=[], memory_records=[], review_items=[], summary=None, decision_transitions=[], memory_uses=[])
    artifacts = [item for item in audit.artifacts if item.phase == "semantic_review"]
    assert len(artifacts) == 1
    assert artifacts[0].status == "available"
    assert artifacts[0].normalization_review.mode == "apply"


def test_unchanged_metadata_is_not_a_business_field_change():
    run = _run()
    change = run.normalization_assistance.observation_changes[0]
    change.before = {**change.after, "file_path": None}
    fields = build_normalization_review_view(run).changes[0].fields
    assert [field.label for field in fields] == ["文件路径"]


def test_saved_review_is_a_distinct_audit_artifact_in_execution_order():
    from soc_agent.demo.corpus_workbench import _audit_bundle, _execution_view

    run = _run()
    execution = _execution_view(alert_id=run.alert_id, run=run, observation=None, replay=None, candidate=None)
    bundle = _audit_bundle(run=run, execution=execution, observation=None, replay=None, candidates=[], memory_records=[], review_items=[], summary=None, decision_transitions=[], memory_uses=[])
    assert bundle.artifacts[3].artifact_id == "semantic-normalization-review"
    assert bundle.artifacts[3].normalization_review.mode == "shadow"
    assert bundle.artifacts[3].payload["result"]["observation_changes"]
    assert [a.sequence for a in bundle.artifacts] == list(range(1, len(bundle.artifacts) + 1))


@pytest.mark.parametrize("status,expected", [("partial", "available"), ("failed", "unavailable"), ("skipped", "unavailable")])
def test_review_execution_display_does_not_treat_notes_as_failure(status, expected):
    from soc_agent.demo.corpus_workbench import _audit_bundle, _execution_view

    run = _run("apply", status)
    run.normalization_assistance.issues = ["上游字段含义待解释。"]
    if status != "partial":
        run.normalization_assistance.observation_changes = []
    before = run.model_dump_json()
    execution = _execution_view(alert_id=run.alert_id, run=run, observation=None, replay=None, candidate=None)
    bundle = _audit_bundle(run=run, execution=execution, observation=None, replay=None, candidates=[], memory_records=[], review_items=[], summary=None, decision_transitions=[], memory_uses=[])
    artifact = bundle.artifacts[3]
    assert artifact.status == expected
    assert artifact.normalization_review.issues == ["上游字段含义待解释。"]
    assert artifact.payload["result"]["status"] == status
    if status == "partial":
        assert artifact.normalization_review.status_label == "核对完成"
        assert "已采用 1 项补充" in artifact.description
    else:
        assert "已采用" not in artifact.description
    assert run.model_dump_json() == before


def test_completed_review_keeps_unadopted_and_coverage_notes_without_fabricating_changes():
    run = _run("apply", "partial")
    run.normalization_assistance.observation_changes = []
    run.normalization_assistance.issues = ["对象 1 未采纳：字段类型或必填内容不符合约定；其他对象继续处理。"]
    run.normalization_assist_request.input_truncated = True
    view = build_normalization_review_view(run)
    assert view.status_label == "核对完成"
    assert view.effect_label == "未修改标准告警，沿用 Adapter 结果"
    assert view.issues == run.normalization_assistance.issues
    assert view.coverage_notes == ["部分日志超过核对输入预算，尾部未核对。"]


def test_live_semantic_review_is_not_mislabeled_as_validation():
    from soc_agent.contracts import AnalysisRunStatus, PipelineStepTrace
    from soc_agent.demo.corpus_workbench import _execution_view

    run = _run()
    run.status = AnalysisRunStatus.RUNNING
    run.normalization_assistance = None
    run.steps = [PipelineStepTrace(step_name="normalization_assist", status="running")]
    execution = _execution_view(alert_id=run.alert_id, run=run, observation=None, replay=None, candidate=None)
    assert execution.current_phase == "semantic_review"
    phase = next(p for p in execution.phases if p.phase == "semantic_review")
    assert phase.label == "语义核对"
