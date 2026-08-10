from __future__ import annotations

import json
from pathlib import Path

from soc_agent.cli import main
from soc_agent.contracts import AnalysisRunStatus, Verdict
from soc_agent.eval import OfflineEvalResponse, load_eval_responses_jsonl, run_offline_eval
from soc_agent.llm import LLMChatResponse
from soc_agent.pipeline.reference_catalog import evidence_ref_for

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _analysis_json(*, verdict: str = "false_positive", trailing_comma: bool = False) -> str:
    suffix = "," if trailing_comma else ""
    evidence_ref = evidence_ref_for("detection.rule_code", "EDR-IOC-001")
    return f"""
    {{
      "schema_version": "soc.analysis_result.v3",
      "verdict": "{verdict}",
      "confidence": 0.81,
      "summary": "录制模型认为该告警可以进入复核。",
      "evidence": [
        {{"evidence_ref": "{evidence_ref}", "source": "detection.rule_code", "description": "规则编号", "value": "EDR-IOC-001"}}
      ],
      "reasoning": [
        {{
          "schema_version": "soc.analysis_reasoning_item.v1",
          "reasoning_id": "R-01",
          "statement": "录制响应用于验证结构化推理输出。",
          "basis": ["current_evidence"],
          "evidence_refs": ["{evidence_ref}"],
          "context_refs": [],
          "confidence": 0.75
        }}
      ],
      "scenario_assessments": [
        {{
          "schema_version": "soc.triage_scenario_assessment.v2",
          "scenario_name": "录制评测场景",
          "scenario_key": "recorded_eval_scenario",
          "is_primary": true,
          "origin": "inferred",
          "confidence": 0.75,
          "activity_stage": "detection_hit",
          "evidence_refs": ["{evidence_ref}"],
          "reasoning_refs": ["R-01"],
          "rationale": "录制响应用于验证结构化输出。",
          "competing_explanations": []
        }}
      ],
      "evidence_gaps": ["离线录制响应不包含实时环境上下文。"],
      "manual_checks": ["人工复核录制响应与样本证据是否一致。"],
      "reason": "这是离线评测使用的可重放模型响应。",
      "recommended_action": "review_recorded_llm_output",
      "knowledge_candidates": []{suffix}
    }}
    """


def test_offline_eval_default_replays_stub_result_through_llm_parser() -> None:
    report = run_offline_eval(
        [
            (str(SAMPLES / "approved_scanner.json"), _sample("approved_scanner.json")),
            (str(SAMPLES / "malicious_ioc.json"), _sample("malicious_ioc.json")),
        ]
    )

    assert report.sample_count == 2
    assert report.stub_success_count == 2
    assert report.llm_success_count == 2
    assert report.parse_success_count == 2
    assert report.verdict_diff_count == 0
    assert report.repair_count == 0
    assert report.llm_needs_review_count == 2
    assert report.grounded_evidence_count + report.ungrounded_evidence_count > 0
    assert report.scenario_assessment_count == 2
    assert {result.model_name for result in report.results} == {"stub-replay"}


def test_offline_eval_reports_verdict_diff_and_json_repair() -> None:
    report = run_offline_eval(
        [(str(SAMPLES / "malicious_ioc.json"), _sample("malicious_ioc.json"))],
        responses={
            "malicious_ioc.json": OfflineEvalResponse(
                sample_id="malicious_ioc.json",
                content=_analysis_json(trailing_comma=True),
                model_name="recorded-model",
            )
        },
        model_name="fallback-model",
    )

    assert report.sample_count == 1
    assert report.llm_success_count == 1
    assert report.parse_success_count == 1
    assert report.repair_count == 1
    assert report.verdict_diff_count == 1

    result = report.results[0]
    assert result.stub_verdict == Verdict.TRUE_POSITIVE
    assert result.llm_verdict == Verdict.FALSE_POSITIVE
    assert result.verdict_changed is True
    assert result.repair_applied is True
    assert result.model_name == "recorded-model"
    assert result.error is None


def test_offline_eval_records_parse_failure_without_crashing_batch() -> None:
    report = run_offline_eval(
        [(str(SAMPLES / "approved_scanner.json"), _sample("approved_scanner.json"))],
        responses={
            "approved_scanner.json": OfflineEvalResponse(
                sample_id="approved_scanner.json",
                content="not json",
            )
        },
    )

    assert report.sample_count == 1
    assert report.llm_success_count == 0
    assert report.parse_success_count == 0
    assert report.failed_count == 1

    result = report.results[0]
    assert result.llm_status == AnalysisRunStatus.FAILED
    assert result.parse_success is False
    assert result.error is not None


def test_offline_eval_can_call_live_client() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, *, model_name):
            self.calls += 1
            assert messages[0]["role"] == "system"
            assert model_name == "deepseek-v4-pro"
            return LLMChatResponse(
                content=_analysis_json(verdict="true_positive"),
                model_name=model_name,
            )

    client = Client()
    report = run_offline_eval(
        [(str(SAMPLES / "malicious_ioc.json"), _sample("malicious_ioc.json"))],
        client=client,
        model_name="deepseek-v4-pro",
    )

    assert client.calls == 1
    assert report.llm_success_count == 1
    assert report.results[0].model_name == "deepseek-v4-pro"


def test_load_eval_responses_jsonl_accepts_object_content(tmp_path: Path) -> None:
    response_path = tmp_path / "responses.jsonl"
    response_path.write_text(
        json.dumps(
            {
                "sample_id": "approved_scanner.json",
                "content": json.loads(_analysis_json()),
                "usage": {"input_tokens": 10},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    responses = load_eval_responses_jsonl(response_path)

    assert set(responses) == {"approved_scanner.json"}
    assert isinstance(responses["approved_scanner.json"].content, str)
    assert responses["approved_scanner.json"].usage == {"input_tokens": 10}


def test_cli_eval_offline_outputs_report(capsys) -> None:
    code = main(["eval", "offline", str(SAMPLES), "--glob", "approved_scanner.json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 0
    assert data["schema_version"] == "soc.offline_eval_report.v2"
    assert data["sample_count"] == 1
    assert data["parse_success_count"] == 1
    assert data["llm_needs_review_count"] == 1
    assert "decision_review_reasons" in data["results"][0]
