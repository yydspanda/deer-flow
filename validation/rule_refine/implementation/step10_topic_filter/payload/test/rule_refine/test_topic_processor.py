"""Synthetic topic acceptance: no model, database, or ZEUS service calls."""

import copy
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_isolated_topic_processor"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
module = importlib.import_module(PACKAGE + ".topic_processor")
ai = importlib.import_module(PACKAGE + ".topic_ai")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget


def rule():
    return {
        "company_code": "synthetic-company",
        "topic": "synthetic-topic",
        "rule_code": "synthetic-rule",
        "rule_config_version": "v1",
        "rule_name": "Synthetic detector",
        "configuration": {"terms": []},
    }


def case():
    def sample(i, role):
        return {
            "alert_id": role + str(i),
            "raw_logs": [
                {
                    "action": "inventory-versions",
                    "unrelated_raw": "NEVER-PERSIST-RAW-" + "x" * 4000,
                }
            ],
            "source_role": role,
        }

    return {
        "rule": rule(),
        "original_rule": rule(),
        "current_samples": [sample(i, "current") for i in range(8)],
        "history_samples": [sample(i, "history") for i in range(2)],
        "coverage": {"current_alert_count": 8, "history_selected_unique_alerts": 2},
        "source": {"kind": "synthetic_fixture"},
        "execute_type": {
            "status": "resolved",
            "values": [3],
            "basis": "current_alerts.alert.executeType",
        },
    }


def signal():
    return {
        "schema_version": "sec_know.rule_burst_signal.v1",
        "mode": "replay",
        "namespace": "synthetic-topic-test",
        "source_app": "zeus",
        "company_code": "synthetic-company",
        "topic": "synthetic-topic",
        "rule_code": "synthetic-rule",
        "signal_id": "s1",
        "sample_alert_ids": ["current0"],
        "current_count": 8,
    }


def reply(request):
    return {
        "status": "proposal",
        "issue": "Synthetic repeated inventory",
        "recommendation": "Filter this specific operation at topic ingress",
        "certainty": "Synthetic evidence only",
        "filter": {
            "topic": "synthetic-topic",
            "A": {"name": "action", "operator": "Eq", "value": "inventory-versions"},
            "expression": "A",
        },
        "sample_alert_ids": [request["context"]["current_samples"][0]["alert_id"]],
        "review_notes": "Same operation may hide evidence used by other detectors",
        "sample_request": None,
        "topic_review": {
            "decision": "submit",
            "field_roles": [
                {
                    "name": "action",
                    "role": "event_field",
                    "basis": "Original event records the operation in action",
                }
            ],
            "other_detection_impact": "All downstream detectors lose matching inventory events; no claim of full topic coverage",
        },
    }


def response(request):
    return {
        "content": json.dumps(reply(request)),
        "transport": {
            "sdk_request_calls": 1,
            "response_received": True,
            "finish_reason": "stop",
            "error_code": None,
            "requested_model": "synthetic-model",
            "usage": {"prompt_tokens": 123},
        },
    }


class TopicProcessorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.case = case()
        self.signal = signal()
        self.load = Mock(side_effect=lambda _: copy.deepcopy(self.case))
        self.catalog = Mock(get_rule=Mock(side_effect=lambda *a, **k: rule()))
        self.generate = Mock(side_effect=response)
        self.submit = Mock(return_value={"success": True, "code": 200, "rule_id": 123})
        self.processor = module.TopicProcessor(
            self.root,
            load_case=self.load,
            load_rules=lambda: self.catalog,
            generate=self.generate,
            model="synthetic-model",
            budget=Budget(),
            submit=self.submit,
        )

    def test_complete_flow_compact_files_and_exact_payload(self):
        result = self.processor.process(self.signal)
        self.assertEqual(result["status"], "received")
        self.assertEqual(self.generate.call_count, 2)
        self.submit.assert_called_once()
        payload = self.submit.call_args.args[0]
        self.assertEqual(payload["executeType"], [3])
        for call in self.generate.call_args_list:
            self.assertEqual(
                call.args[0]["context"]["coverage"]["target_executeType"], [3]
            )
        self.assertEqual(set(payload), {"topic", "A", "expression", "executeType"})
        self.assertTrue(self.processor.verify_durable_result(self.signal, result))
        files = [p for p in self.root.rglob("*") if p.is_file()]
        text = "".join(p.read_text() for p in files)
        self.assertNotIn("NEVER-PERSIST-RAW", text)
        self.assertNotIn('"raw_logs"', text)
        self.assertNotIn('"messages"', text)
        self.assertLess(sum(p.stat().st_size for p in files), 64 * 1024)
        self.assertNotIn("case.json", [p.name for p in files])

    def test_completed_retry_calls_neither_model_nor_zeus(self):
        first = self.processor.process(self.signal)
        second = self.processor.process(self.signal)
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(second["model_calls_this_attempt"], 0)
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(self.submit.call_count, 1)

    def test_large_materials_do_not_expand_saved_record(self):
        self.case["current_samples"] = [
            {
                "alert_id": f"large-{i}",
                "raw_logs": [
                    {
                        "action": "inventory-versions",
                        "large_unrelated": "NEVER-PERSIST-RAW-" + "x" * 40000,
                    }
                ],
            }
            for i in range(100)
        ]
        self.case["coverage"]["current_alert_count"] = 100
        result = self.processor.process(self.signal)
        self.assertEqual(result["status"], "received")
        paths = [p for p in self.root.rglob("*") if p.is_file()]
        self.assertLess(sum(p.stat().st_size for p in paths), 64 * 1024)
        self.assertNotIn("NEVER-PERSIST-RAW", "".join(p.read_text() for p in paths))

    def test_new_burst_same_filter_deduplicates_zeus(self):
        self.processor.process(self.signal)
        self.signal["signal_id"] = "s2"
        result = self.processor.process(self.signal)
        self.assertEqual(result["status"], "received")
        self.assertEqual(self.generate.call_count, 4)
        self.assertEqual(self.submit.call_count, 1)

    def test_crash_after_delivery_saved_resumes_without_new_post(self):
        with patch.object(
            self.processor, "_finish", side_effect=RuntimeError("synthetic exit")
        ):
            with self.assertRaises(RuntimeError):
                self.processor.process(self.signal)
        result = self.processor.process(self.signal)
        self.assertEqual(result["status"], "received")
        self.assertTrue(self.processor.verify_durable_result(self.signal, result))
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(self.submit.call_count, 1)

    def test_unknown_or_mixed_type_never_calls_model_or_submits(self):
        for values in ([], [0, 3], [-1], [True]):
            with self.subTest(values=values):
                self.signal["signal_id"] = repr(values)
                self.case["execute_type"]["values"] = values
                result = self.processor.process(self.signal)
                self.assertEqual(result["status"], "manual_review")
        self.generate.assert_not_called()
        self.submit.assert_not_called()

    def test_generation_failure_does_not_review_or_submit(self):
        self.generate.return_value = {
            "content": "{}",
            "transport": {"finish_reason": "stop"},
        }
        self.generate.side_effect = None
        result = self.processor.process(self.signal)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.generate.call_count, 1)
        self.submit.assert_not_called()

    def test_lease_or_transport_failure_never_creates_rule(self):
        self.generate.side_effect = TimeoutError("DO-NOT-PERSIST-ERROR-BODY")
        result = self.processor.process(self.signal)
        self.assertEqual(result["status"], "blocked")
        self.processor.process(self.signal)
        self.assertEqual(self.generate.call_count, 1)
        self.submit.assert_not_called()
        self.assertNotIn(
            "DO-NOT-PERSIST", "".join(p.read_text() for p in self.root.rglob("*.json"))
        )

    def test_review_withholds_or_uncertain_field_prevents_submission(self):
        def answer(request):
            data = reply(request)
            if request["phase"] == "review":
                data["topic_review"]["decision"] = "withhold"
            return {"content": json.dumps(data), "transport": {"finish_reason": "stop"}}

        self.generate.side_effect = answer
        self.assertEqual(self.processor.process(self.signal)["status"], "manual_review")
        self.submit.assert_not_called()

    def test_changed_rule_snapshot_prevents_submission(self):
        self.catalog.get_rule.return_value = {**rule(), "rule_config_version": "v2"}
        self.catalog.get_rule.side_effect = None
        self.assertEqual(self.processor.process(self.signal)["status"], "manual_review")
        self.submit.assert_not_called()

    def test_submit_timeout_is_not_retried(self):
        self.submit.side_effect = TimeoutError()
        result = self.processor.process(self.signal)
        self.assertEqual(result["status"], "blocked")
        self.processor.process(self.signal)
        self.assertEqual(self.submit.call_count, 1)

    def test_modified_artifact_is_not_acknowledged(self):
        result = self.processor.process(self.signal)
        path = Path(result["output_dir"]) / "recommendation.json"
        path.write_text("{}")
        with self.assertRaises(ValueError):
            self.processor.verify_durable_result(self.signal, result)


class TopicPromptTests(unittest.TestCase):
    def build(self, **kw):
        return ai.build_request(
            rule(), case()["current_samples"], case()["history_samples"], {}, **kw
        )

    def test_explicit_topic_scope_not_old_rule_exclusion_formula(self):
        request = self.build()
        system = request["messages"][0]["content"]
        self.assertIn("所有", system)
        self.assertIn("检测前", system)
        self.assertNotIn("原规则 &&", system)
        self.assertEqual(
            request["context"]["optimization_scope"], "topic_before_detection"
        )
        self.assertFalse(request["context"]["other_rules_recalled"])

    def test_review_does_not_receive_draft_prose(self):
        draft = reply(self.build())
        draft["issue"] = "DO-NOT-PROMPT-DRAFT-REASON"
        request = self.build(phase="review", draft=draft)
        self.assertNotIn("DO-NOT-PROMPT", json.dumps(request))

    def test_missing_topic_review_or_bad_field_role_cannot_be_ready(self):
        request = self.build(phase="review", draft=reply(self.build()))
        data = reply(request)
        del data["topic_review"]
        with self.assertRaises(ValueError):
            ai.parse_reply(json.dumps(data), request=request)
        data = reply(request)
        data["topic_review"]["field_roles"][0]["role"] = "detection_metadata"
        parsed = ai.parse_reply(json.dumps(data), request=request)
        self.assertFalse(parsed["topic_submission_ready"])

    def test_detection_results_rejected_and_unknown_topic_behavior_allowed(self):
        actual = rule()
        actual["topic"] = "security_qthids"
        samples = [
            {
                "alert_id": "x",
                "raw_logs": [
                    {"detail_hit_rule_name": "detector", "action": "inventory-versions"}
                ],
            }
        ]
        req = ai.build_request(
            actual, samples, [], {}, phase="review", draft=reply(self.build())
        )
        data = reply(req)
        data["filter"]["topic"] = actual["topic"]
        data["filter"]["A"]["name"] = "detail_hit_rule_name"
        data["topic_review"]["field_roles"][0]["name"] = "detail_hit_rule_name"
        self.assertFalse(
            ai.parse_reply(json.dumps(data), request=req)["topic_submission_ready"]
        )


if __name__ == "__main__":
    unittest.main()
