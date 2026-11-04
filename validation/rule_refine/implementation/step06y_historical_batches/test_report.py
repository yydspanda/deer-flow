"""Report coverage uses source identities rather than batch-level sums."""

import copy
import unittest
from dataclasses import asdict

from test_runner import context, limits_module, load_tools


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.report = load_tools().batch_report

    def summary(self, contexts, outcomes):
        plan = {
            "batches": contexts,
            "coverage": {
                "file_rows": 214,
                "inspected_rows": 214,
                "all_eligible_logs_planned": True,
            },
        }
        result = self.report.summarize(
            plan,
            outcomes,
            model="Pub_DeepSeek_V4_Flash",
            limits=asdict(limits_module.Limits()),
            output_dir="synthetic",
        )
        return plan, result

    def outcome(self, index, **changes):
        return {
            "batch_index": index,
            "status": "passed",
            "analysis_status": "proposal",
            "sdk_request_calls": 1,
            "input_bytes": 100,
            "response_received": True,
            "execution_started": True,
            "execution_finished": True,
            **changes,
        }

    def test_shared_alert_and_same_source_log_are_deduplicated(self):
        contexts = [
            context(1, log_index=0),
            context(2, log_index=1),
            context(3, log_index=0),
        ]
        _, summary = self.summary(contexts, [self.outcome(i) for i in range(1, 4)])
        self.assertEqual(
            summary["planned_coverage"], {"unique_alerts": 1, "unique_logs": 2}
        )
        self.assertEqual(
            summary["valid_result_coverage"], {"unique_alerts": 1, "unique_logs": 2}
        )
        self.assertEqual(summary["sdk_request_calls"], 3)

    def test_missing_lineage_is_not_silently_counted(self):
        material = context(1)
        material["samples"][0]["log_refs"] = []
        with self.assertRaisesRegex(ValueError, "log_lineage_mismatch"):
            self.report.identities(material)

    def test_estimate_and_provider_usage_are_separate_and_missing_usage_is_unknown(
        self,
    ):
        contexts = [context(1), context(2)]
        outcomes = [
            self.outcome(1, estimated_input_tokens=400, usage={"prompt_tokens": 123}),
            self.outcome(2, estimated_input_tokens=500, usage={"prompt_tokens": 234}),
        ]
        _, summary = self.summary(contexts, outcomes)
        self.assertEqual(summary["planned_estimated_input_tokens"], 900)
        self.assertEqual(summary["reported_prompt_tokens"], 357)
        self.assertEqual(summary["prompt_usage_missing_batches"], 0)
        outcomes[1]["usage"] = {"prompt_tokens": None}
        _, summary = self.summary(contexts, outcomes)
        self.assertIsNone(summary["reported_prompt_tokens"])
        self.assertEqual(summary["prompt_usage_missing_batches"], 1)

    def test_mixed_results_preserved_without_filter_merging_or_extra_model(self):
        contexts = [context(1), context(2)]
        plan, summary = self.summary(
            contexts,
            [self.outcome(1), self.outcome(2, analysis_status="insufficient_evidence")],
        )
        analyses = [
            {
                "status": "proposal",
                "issue": "业务属性仍待确认",
                "advice": "确认后添加条件A",
                "filter": {
                    "expression": "A",
                    "A": {"name": "path", "value": "/health", "operator": "equal"},
                },
                "evaluation": {
                    "counts": {"matched": 1, "not_matched": 0, "unknown": 0}
                },
            },
            {
                "status": "insufficient_evidence",
                "issue": "缺少资产证据",
                "advice": "补充资产属性",
                "filter": None,
            },
        ]
        original = copy.deepcopy(analyses)
        text = self.report.report_text(plan, summary, analyses)
        self.assertIn("有条件建议", text)
        self.assertIn("证据不足", text)
        self.assertIn("补充资产属性", text)
        self.assertIn("不合并不同批次的过滤条件", text)
        self.assertIn("不代表误报率或线上降量率", text)
        self.assertNotIn("100%", text)
        self.assertEqual(analyses, original)
        self.assertEqual(summary["model_synthesis_calls"], 0)
        self.assertEqual(
            summary["batch_counts"]["valid_analysis_statuses"],
            {"proposal": 1, "insufficient_evidence": 1},
        )

    def test_not_started_and_unknown_call_never_imply_coverage(self):
        plan, summary = self.summary(
            [context(1), context(2)],
            [
                self.outcome(
                    1,
                    status="interrupted",
                    sdk_request_calls=None,
                    response_received=False,
                    execution_finished=False,
                ),
                self.outcome(
                    2,
                    status="not_started",
                    sdk_request_calls=0,
                    response_received=False,
                    execution_started=False,
                    execution_finished=False,
                ),
            ],
        )
        self.assertEqual(summary["planned_coverage"]["unique_logs"], 2)
        self.assertEqual(summary["request_attempted_coverage"]["unique_logs"], 0)
        self.assertEqual(summary["unknown_request_coverage"]["unique_logs"], 1)
        self.assertEqual(summary["valid_result_coverage"]["unique_logs"], 0)
        self.assertIsNone(summary["sdk_request_calls"])
        self.assertFalse(summary["all_planned_batches_valid"])
        text = self.report.report_text(plan, summary, [{}, {}])
        self.assertIn("本批未执行", text)

    def test_manual_review_counts_valid_answer_but_is_explicitly_not_evaluated(self):
        plan, summary = self.summary(
            [context(1)],
            [self.outcome(1, status="manual_review", analysis_status="manual_review")],
        )
        text = self.report.report_text(
            plan, summary, [{"status": "manual_review", "filter": {"expression": "A"}}]
        )
        self.assertEqual(summary["status"], "passed")
        self.assertIn("本地试算器未完成", text)
        self.assertEqual(summary["batch_counts"]["by_status"], {"manual_review": 1})


if __name__ == "__main__":
    unittest.main()
