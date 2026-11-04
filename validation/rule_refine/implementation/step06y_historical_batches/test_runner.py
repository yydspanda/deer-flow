"""Offline orchestration tests with synthetic samples and one-call fake executors."""

import copy
import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock, patch


HERE = Path(__file__).resolve().parent
PACKAGE = "_historical_batches_runner_tests"
package = ModuleType(PACKAGE)
package.__path__ = [
    str(HERE.parent / "step06w_unified_limits/payload/my_workflows/zeus/rule_refine")
]
sys.modules[PACKAGE] = package
analysis = importlib.import_module(PACKAGE + ".analysis")
limits_module = importlib.import_module(PACKAGE + ".limits")
filter_evaluation = importlib.import_module(PACKAGE + ".filter_evaluation")


def load_tools():
    paths = {
        "inventory": HERE.parent / "step06o_ingress_readiness/materials_check.py",
        "context_budget": HERE / "context_budget.py",
        "historical_review": HERE / "review.py",
        "historical_adapter": HERE / "adapter.py",
        "historical_materials": HERE.parent / "step06x_historical_review/materials.py",
        "single_runner": HERE.parent / "step06x_historical_review/runner.py",
        "batch_report": HERE / "report.py",
        "batch_runner": HERE / "runner.py",
    }
    result = {}
    with patch.dict(sys.modules, {"batch_materials": ModuleType("batch_materials")}):
        for name, path in paths.items():
            mod = ModuleType(name)
            mod.__file__ = str(path)
            sys.modules[name] = mod
            exec(
                compile(path.read_text(encoding="utf-8"), str(path), "exec"),
                mod.__dict__,
            )
            result[name] = mod
    return NS(**result)


def context(index, *, alert="shared-alert", log_index=None):
    log_index = index if log_index is None else log_index
    return {
        "batch_index": index,
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "rule": {
            "rule_code": "DEMO",
            "rule_name": "人工测试",
            "topic": "demo",
            "company_code": "company",
            "rule_config_version": "v1",
        },
        "coverage": {"included_alerts": 1, "included_logs": 1},
        "samples": [
            {
                "alert_id": alert,
                "raw_logs": [{"path": "/health"}],
                "log_refs": [
                    {
                        "source_row_position_zero_based": 2,
                        "hit_index": 0,
                        "raw_log_index": log_index,
                        "log_sha256": "synthetic",
                    }
                ],
            }
        ],
    }


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tools = load_tools()
        self.runner = self.tools.batch_runner
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_limits = limits_module.Limits(
            input_kib=256, output_kib=128, timeout_seconds=300
        )
        self.limits = self.tools.context_budget.from_limits(self.source_limits)

    def prepared(self, count=3):
        contexts = [context(i) for i in range(1, count + 1)]
        loaded = NS(analysis=analysis, limits=limits_module)
        batches = [
            NS(
                context=c,
                request=self.tools.historical_review.build_request(
                    c, analysis, limits=self.limits
                ),
                loaded=loaded,
                model="Pub_DeepSeek_V4_Flash",
                limits=self.limits,
            )
            for c in contexts
        ]
        return NS(
            plan={
                "batches": contexts,
                "source": {"file_sha256": "synthetic"},
                "coverage": {
                    "file_rows": 214,
                    "inspected_rows": 214,
                    "eligible_unique_alerts": 1,
                    "eligible_logs": count,
                    "all_eligible_logs_planned": True,
                },
            },
            batches=batches,
            root=self.root,
            pins={},
            snapshot={},
            loaded=loaded,
            limits=self.limits,
            model="Pub_DeepSeek_V4_Flash",
        )

    def executor(self, statuses=None):
        calls = []
        statuses = statuses or ["proposal", "insufficient_evidence", "no_change"]

        def invoke(batch, directory):
            index = len(calls)
            calls.append(
                (batch.model, asdict(batch.limits), batch.request["prompt_sha256"])
            )
            result = {
                "status": statuses[index],
                "issue": f"问题{index}",
                "advice": f"建议{index}",
                "filter": None,
            }
            if statuses[index] == "proposal":
                result.update(
                    filter={
                        "topic": "demo",
                        "expression": "A",
                        "A": {"name": "path", "operator": "equal", "value": "/health"},
                    },
                    evaluation={
                        "counts": {"matched": 1, "not_matched": 0, "unknown": 0}
                    },
                )
            self.tools.single_runner.save(directory, "analysis.json", result)
            return {
                "status": "passed",
                "analysis_status": statuses[index],
                "sdk_request_calls": 1,
                "response_received": True,
                "format_valid": True,
                "configuration_unchanged": True,
            }

        return calls, invoke

    def run_batches(self, p, execute, verify=lambda p: None):
        with redirect_stdout(io.StringIO()):
            return self.runner.execute_all(p, self.root, execute=execute, verify=verify)

    def test_all_requests_staged_before_first_sdk_and_one_execution_each(self):
        p = self.prepared()
        calls, execute = self.executor()

        def checked(batch, directory):
            for i in range(1, 4):
                child = self.root / f"batch-{i:03d}"
                for name in (
                    "context.json",
                    "request.json",
                    "source.json",
                    "audit.json",
                    "outcome.json",
                ):
                    self.assertTrue((child / name).is_file())
                self.assertEqual((child / "request.json").stat().st_mode & 0o777, 0o600)
            return execute(batch, directory)

        summary = self.run_batches(p, checked)
        self.assertEqual(len(calls), 3)
        self.assertEqual(summary["sdk_request_calls"], 3)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["model_synthesis_calls"], 0)
        self.assertEqual(
            summary["batch_counts"]["valid_analysis_statuses"],
            {"proposal": 1, "insufficient_evidence": 1, "no_change": 1},
        )

    def test_batch_failure_stops_later_calls_and_keeps_success(self):
        p = self.prepared()
        calls, execute = self.executor()

        def fail_second(batch, directory):
            result = execute(batch, directory)
            if len(calls) == 2:
                result.update(
                    status="blocked",
                    analysis_status="analysis_failed",
                    format_valid=False,
                    error_code="invalid_model_output",
                )
            return result

        summary = self.run_batches(p, fail_second)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [row["status"] for row in summary["batches"]],
            ["passed", "blocked", "not_started"],
        )
        self.assertEqual(
            summary["valid_result_coverage"], {"unique_alerts": 1, "unique_logs": 1}
        )
        self.assertFalse(summary["all_planned_batches_valid"])
        self.assertEqual(summary["stop_reason"], "invalid_model_output")

    def test_model_limits_and_prompt_frozen_for_every_batch(self):
        p = self.prepared()
        calls, execute = self.executor()
        self.run_batches(p, execute)
        self.assertTrue(
            all(
                model == p.model and limits == asdict(self.limits)
                for model, limits, _ in calls
            )
        )
        self.assertEqual(
            [digest for _, _, digest in calls],
            [batch.request["prompt_sha256"] for batch in p.batches],
        )

    def test_changed_in_memory_context_stops_before_any_call(self):
        p = self.prepared()
        p.batches[0].context = copy.deepcopy(p.batches[0].context)
        p.batches[0].context["samples"][0]["raw_logs"][0]["path"] = "/changed"
        execute = Mock()
        summary = self.run_batches(p, execute)
        execute.assert_not_called()
        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["sdk_request_calls"], 0)

    def test_configuration_change_after_call_stops_next_batch(self):
        p = self.prepared()
        calls, execute = self.executor()
        verify = Mock(side_effect=[None, ValueError("configuration_changed")])
        summary = self.run_batches(p, execute, verify)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            summary["stop_reason"], "post_request_configuration_check_failed"
        )
        self.assertEqual(summary["response_received_coverage"]["unique_logs"], 1)
        self.assertEqual(summary["valid_result_coverage"]["unique_logs"], 0)

    def test_interruption_after_started_marker_records_unknown_without_retry(self):
        p = self.prepared()
        calls = []

        def interrupt(batch, directory):
            calls.append(batch)
            self.tools.single_runner.save(
                directory, "request-started.json", {"max_sdk_calls": 1}
            )
            raise KeyboardInterrupt

        summary = self.run_batches(p, interrupt)
        self.assertEqual(len(calls), 1)
        self.assertEqual(summary["status"], "interrupted")
        self.assertIsNone(summary["sdk_request_calls"])
        self.assertEqual(summary["unknown_request_batches"], 1)
        self.assertEqual(summary["unknown_request_coverage"]["unique_logs"], 1)
        self.assertEqual(
            json.loads((self.root / "summary.json").read_text())["status"],
            "interrupted",
        )

    def test_pre_sdk_error_preserves_zero_known_calls(self):
        summary = self.run_batches(
            self.prepared(), Mock(side_effect=ImportError("no dependency"))
        )
        self.assertEqual(summary["sdk_request_calls"], 0)
        self.assertEqual(summary["unknown_request_batches"], 0)
        self.assertEqual(
            summary["batch_counts"]["by_status"], {"failed": 1, "not_started": 2}
        )

    def test_report_read_error_preserves_known_completed_sdk_count(self):
        def malformed_artifact(batch, directory):
            self.tools.single_runner.save(directory, "request-started.json", {})
            self.tools.single_runner.save(directory, "analysis.json", b"not-json")
            return {
                "status": "passed",
                "analysis_status": "proposal",
                "sdk_request_calls": 1,
                "response_received": True,
                "format_valid": True,
                "configuration_unchanged": True,
            }

        summary = self.run_batches(self.prepared(), malformed_artifact)
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertEqual(summary["unknown_request_batches"], 0)
        self.assertEqual(
            summary["batch_counts"]["by_status"], {"failed": 1, "not_started": 2}
        )

    def test_cancel_means_zero_calls_and_no_directories(self):
        p = self.prepared()
        with (
            patch.object(self.runner, "prepare", return_value=p),
            patch.object(self.runner, "execute_all") as execute,
            patch("builtins.open", return_value=io.StringIO("NO\n")),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(self.runner.main({}), 0)
        execute.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_non_local_stops_before_application_imports(self):
        with (
            patch.dict(os.environ, {"env_profile": "PRD"}),
            patch.object(self.tools.single_runner, "modules") as modules,
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(self.runner.main({}), 1)
        modules.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["error_code"], "local_only")

    def test_configuration_file_change_detected_and_optional_limits_tracked(self):
        (self.root / "config").mkdir()
        model_path = self.root / self.runner.CONFIG_FILES[0]
        model_path.write_text('{"model":"one"}', encoding="utf-8")
        p = self.prepared()
        p.snapshot = self.runner.configuration_snapshot(self.root, {})
        p.csv = self.root / "rules.csv"
        p.loaded.csv_config_source = NS(
            CsvRuleConfigSource=lambda _: NS(
                get_rule=lambda *a, **kw: p.plan["batches"][0]["rule"]
            )
        )
        self.runner.verify_configuration(p)
        model_path.write_text('{"model":"two"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configuration_changed"):
            self.runner.verify_configuration(p)
        model_path.write_text('{"model":"one"}', encoding="utf-8")
        (self.root / self.runner.CONFIG_FILES[1]).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configuration_changed"):
            self.runner.verify_configuration(p)

    def test_target_rule_source_drift_detected(self):
        p = self.prepared()
        p.csv = self.root / "rules.csv"
        fresh = {**p.plan["batches"][0]["rule"], "rule_config_version": "v2"}
        p.loaded.csv_config_source = NS(
            CsvRuleConfigSource=lambda _: NS(get_rule=lambda *a, **kw: fresh)
        )
        with (
            patch.object(self.runner, "configuration_snapshot", return_value={}),
            self.assertRaisesRegex(ValueError, "rule_configuration_changed"),
        ):
            self.runner.verify_configuration(p)

    def test_terminal_summary_excludes_raw_batch_evidence(self):
        calls, execute = self.executor()
        summary = self.run_batches(self.prepared(), execute)
        compact = self.runner.terminal_summary(summary)
        self.assertNotIn("batches", compact)
        self.assertNotIn("source_coverage", compact)
        self.assertEqual(compact["model"], "Pub_DeepSeek_V4_Flash")
        self.assertEqual(compact["source"]["inspected_rows"], 214)

    def test_prepare_passes_entire_frame_and_selected_model_to_all_frozen_requests(
        self,
    ):
        import pandas as pd

        (self.root / "config").mkdir()
        (self.root / self.runner.CONFIG_FILES[0]).write_text(
            '{"model":"Pub_DeepSeek_V4_Flash"}', encoding="utf-8"
        )
        (self.root / self.runner.CONFIG_FILES[1]).write_text(
            json.dumps(asdict(self.source_limits)), encoding="utf-8"
        )
        sample = self.root / "nas/original/full_alert_2026_month_forth_sample_200.pkl"
        sample.parent.mkdir(parents=True)
        csv = self.root / "sec-rule-refine-data/rules.csv"
        csv.parent.mkdir()
        csv.write_bytes(b"synthetic-csv")
        payloads = []
        for number in range(214):
            identity = f"synthetic-{number}"
            payloads.append(
                {
                    "app_code": "zeus",
                    "flow_id": "alert_agent",
                    "alert_id": identity,
                    "alert_data": {
                        "alert": {
                            "alertId": identity,
                            "hitLog": [
                                {
                                    "topic": "ptp-nids",
                                    "ruleCode": "RPAADM_002627",
                                    "zeusRawLogs": [{"path": "/health"}],
                                }
                            ],
                        },
                        "relatedAlertList": [],
                    },
                }
            )
        frame = pd.DataFrame({"alert_full_data": payloads})
        # Match the reviewed historical file's object Index, including on pandas 3.
        frame.columns = pd.Index(["alert_full_data"], dtype=object)
        frame.to_pickle(sample)
        rule = {
            "company_code": "SYNTHETIC",
            "topic": "ptp-nids",
            "rule_code": "RPAADM_002627",
            "rule_name": "人工规则",
            "rule_config_version": "synthetic-v1",
            "source": {
                "kind": "csv_snapshot",
                "file_sha256": hashlib.sha256(csv.read_bytes()).hexdigest(),
            },
            "configuration": {"terms": [{"condition": "原条件"}]},
        }
        rules = NS(
            file_sha256=hashlib.sha256(csv.read_bytes()).hexdigest(),
            get_rule=lambda *a, **kw: rule,
        )
        loaded = NS(
            analysis=analysis,
            filter_evaluation=filter_evaluation,
            limits=limits_module,
            csv_config_source=NS(CsvRuleConfigSource=lambda _: rules),
        )
        planner_module = ModuleType("prepare_planner")
        planner_module.__file__ = str(HERE / "materials.py")
        with patch.dict(
            sys.modules,
            {
                "historical_materials": self.tools.historical_materials,
                "historical_review": self.tools.historical_review,
            },
        ):
            exec(
                compile(
                    (HERE / "materials.py").read_text(encoding="utf-8"),
                    str(HERE / "materials.py"),
                    "exec",
                ),
                planner_module.__dict__,
            )
        planner = Mock(wraps=planner_module.build_plan)
        selected = NS(
            model="Pub_DeepSeek_V4_Flash",
            key="test",
            endpoint="http://localhost",
            rc=NS(),
            policy=NS(),
        )
        with (
            patch.dict(os.environ, {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}),
            patch.object(self.runner.Path, "home", return_value=self.root),
            patch.object(
                self.tools.historical_materials,
                "SAMPLE_SHA",
                hashlib.sha256(sample.read_bytes()).hexdigest(),
            ),
            patch.object(self.tools.historical_materials, "CSV_SHA", rules.file_sha256),
            patch.object(self.tools.single_runner, "modules", return_value=loaded),
            patch.object(
                self.runner.batch_materials, "build_plan", planner, create=True
            ),
            patch.object(self.runner, "runtime", return_value=selected) as runtime,
        ):
            prepared = self.runner.prepare(self.root, {})
        pd.testing.assert_frame_equal(planner.call_args.args[0], frame)
        self.assertEqual(len(planner.call_args.args[0]), 214)
        self.assertEqual(runtime.call_args.args[2]["model"], "Pub_DeepSeek_V4_Flash")
        self.assertEqual(prepared.plan["coverage"]["inspected_rows"], 214)
        self.assertEqual(prepared.plan["coverage"]["planned_unique_alerts"], 214)
        self.assertEqual(len(prepared.batches), 1)
        self.assertTrue(
            all(
                batch.model == selected.model and batch.limits == self.limits
                for batch in prepared.batches
            )
        )
        self.assertTrue(
            all(
                batch.request["estimated_input_tokens"]
                <= self.limits.input_token_budget
                for batch in prepared.batches
            )
        )


if __name__ == "__main__":
    unittest.main()
