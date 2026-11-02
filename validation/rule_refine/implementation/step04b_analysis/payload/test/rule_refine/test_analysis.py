"""规则建议生成边界测试；使用人工回复，不连接模型或外部服务。"""

import copy
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

fixture_path = Path(__file__).with_name("test_materials.py")
spec = importlib.util.spec_from_file_location("_rule_refine_analysis_fixtures", fixture_path)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
analysis = importlib.import_module(fixtures.package.__name__ + ".analysis")
contracts = importlib.import_module(fixtures.package.__name__ + ".contracts")


def materials(rows=None):
    rows = [fixtures.row()] if rows is None else rows
    provider = fixtures.rules()
    provider.get_rule.return_value["configuration"] = {"terms": [{"expression": "A"}]}
    ids = list(dict.fromkeys(item["alert_id"] for item in rows))
    return fixtures.build(fixtures.signal(ids), provider, rows, source_kind="synthetic_fixture")


def draft():
    return {
        "status": "proposal",
        "issue": "人工示例：同类业务访问集中触发告警",
        "recommendation": "人工示例：运营确认属于正常业务后，增加该场景排除条件",
        "certainty": "人工样本仅验证流程；正常业务属性仍需运营确认",
        "sample_alert_ids": ["101"],
        "filter": {
            "topic": "demo-topic",
            "expression": "A",
            "A": {"name": "host", "value": "synthetic.invalid", "operator": "equal"},
        },
    }


class AnalysisTests(unittest.TestCase):
    def call(self, data=None, answer=None):
        generate = Mock(return_value=json.dumps(draft() if answer is None else answer, ensure_ascii=False))
        result = analysis.analyze_materials(materials() if data is None else data, generate)
        return result, generate

    def assert_failed(self, result, generate):
        self.assertEqual(result["status"], "analysis_failed")
        self.assertIsNone(result["recommendation"])
        self.assertTrue(result["error_code"])
        self.assertEqual(result["generation_calls"], 1)
        self.assertEqual(result["model_calls"], 0)
        generate.assert_called_once()

    def test_proposal_identity_and_evidence_are_filled_from_materials(self):
        data = materials()
        result, generate = self.call(data)
        self.assertEqual(result["status"], "proposal")
        record = contracts.RuleRecommendation.model_validate(result["recommendation"])
        self.assertEqual((record.company_code, record.rule_code), ("demo-company", "DEMO_RULE"))
        self.assertEqual(record.rule_config_version, "demo-config-v1")
        self.assertEqual(record.evidence.alert_count, 1000)
        self.assertEqual(record.evidence.sample_alert_ids, ["101"])
        self.assertFalse(record.automatic_apply)
        self.assertEqual(result["validation_scope"], "structure_and_individual_conditions_only")
        self.assertEqual(result["engine_compatibility"], "not_verified")
        self.assertEqual((result["generation_calls"], result["model_calls"]), (1, 0))
        generate.assert_called_once_with(analysis.build_analysis_request(data)["messages"])

    def test_request_is_stable_bounded_and_does_not_mutate_materials(self):
        data = materials()
        before = copy.deepcopy(data)
        first = analysis.build_analysis_request(data)
        self.assertEqual(first, analysis.build_analysis_request(data))
        self.assertEqual(len(first["prompt_sha256"]), 64)
        self.assertEqual([item["role"] for item in first["messages"]], ["system", "user"])
        self.assertEqual(json.loads(first["messages"][1]["content"]), first["context"])
        self.call(data)
        self.assertEqual(data, before)

    def test_empty_materials_or_oversized_configuration_do_not_call_generate(self):
        empty = materials([])
        huge = materials()
        huge["rule"]["configuration"] = {"large": "x" * (1024 * 1024)}
        for data in (empty, huge):
            with self.subTest(kind=data["status"]):
                self.assertIsNone(analysis.build_analysis_request(data))
                result, generate = self.call(data)
                self.assertEqual(result["status"], "insufficient_evidence")
                self.assertIsNone(result["recommendation"])
                self.assertEqual((result["generation_calls"], result["model_calls"]), (0, 0))
                generate.assert_not_called()

    def test_nonproposal_states_do_not_emit_a_recommendation(self):
        for status in ("no_change", "insufficient_evidence", "needs_existing_condition_change"):
            answer = {**draft(), "status": status, "sample_alert_ids": [], "filter": None}
            result, generate = self.call(answer=answer)
            self.assertEqual(result["status"], status)
            self.assertIsNone(result["recommendation"])
            generate.assert_called_once()

    def test_unknown_fields_ids_and_topic_are_rejected(self):
        for update in ({"company_code": "forged"}, {"sample_alert_ids": ["expired-999"]}):
            self.assert_failed(*self.call(answer={**draft(), **update}))
        for name, value in (("name", "invented_field"), ("value", "not-in-any-log")):
            answer = draft()
            answer["filter"]["A"][name] = value
            self.assert_failed(*self.call(answer=answer))
        answer = draft()
        answer["filter"]["topic"] = "other-topic"
        self.assert_failed(*self.call(answer=answer))

    def test_empty_bad_duplicate_key_and_oversized_json_fail_without_retry(self):
        normal = json.dumps(draft())
        duplicate = normal.replace('"status": "proposal"', '"status": "no_change", "status": "proposal"')
        nested_duplicate = normal.replace('"name": "host"', '"name": "wrong", "name": "host"')
        for reply in ("", "{", "[]", "```json\n" + normal + "\n```", duplicate, nested_duplicate, "x" * 100000):
            with self.subTest(length=len(reply)):
                generate = Mock(return_value=reply)
                result = analysis.analyze_materials(materials(), generate)
                self.assert_failed(result, generate)

    def test_generation_failure_is_not_reported_as_no_change(self):
        generate = Mock(side_effect=RuntimeError("人工注入错误"))
        self.assert_failed(analysis.analyze_materials(materials(), generate), generate)

    def test_supported_operators_preserve_the_existing_contract(self):
        for operator, value in (("equal", "synthetic.invalid"), ("contain", "synthetic."), ("notcontain", "malware")):
            answer = draft()
            answer["filter"]["A"].update(operator=operator, value=value)
            result, _ = self.call(answer=answer)
            self.assertEqual(result["status"], "proposal")
            self.assertEqual(result["recommendation"]["filter"]["A"]["operator"], operator)

    def test_missing_field_in_referenced_sample_cannot_prove_notcontain(self):
        other = fixtures.row("102", "task-b")
        other["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"][0]["action"] = "allowed"
        answer = draft()
        answer["filter"]["A"].update(name="action", operator="notcontain", value="blocked")
        self.assert_failed(*self.call(materials([fixtures.row(), other]), answer))

    def test_omitted_whole_log_cannot_ground_a_condition_from_hidden_values(self):
        data = materials()
        data["samples"][0]["raw_logs"].append({"host": "hidden.invalid", "large": "x" * 65536})
        request = analysis.build_analysis_request(data)
        self.assertGreaterEqual(request["omitted_logs"], 1)
        self.assertNotIn("hidden.invalid", request["messages"][1]["content"])
        answer = draft()
        answer["filter"]["A"]["value"] = "hidden.invalid"
        self.assert_failed(*self.call(data, answer))

    def test_unprojected_sample_reference_is_rejected(self):
        data = materials([fixtures.row(), fixtures.row("102", "task-b")])
        data["samples"][1]["raw_logs"] = [{"host": "hidden.invalid", "large": "x" * 65536}]
        self.assertIsNotNone(analysis.build_analysis_request(data))
        answer = draft()
        answer["sample_alert_ids"] = ["102"]
        answer["filter"]["A"]["value"] = "hidden.invalid"
        self.assert_failed(*self.call(data, answer))

    def test_malformed_material_shapes_or_windows_fail_before_generation(self):
        bad_sample = materials()
        bad_sample["samples"] = [None]
        bad_window = materials()
        bad_window["window"]["count"] = False
        for data in ([], 1, {}, bad_sample, bad_window):
            generate = Mock()
            result = analysis.analyze_materials(data, generate)
            self.assertEqual((result["status"], result["error_code"]), ("analysis_failed", "invalid_materials"))
            self.assertEqual(result["generation_calls"], 0)
            generate.assert_not_called()

    def test_separate_condition_support_is_not_reported_as_combined_engine_match(self):
        data = materials()
        data["samples"][0]["raw_logs"] = [{"host": "synthetic.invalid"}, {"action": "read"}]
        answer = draft()
        answer["filter"].update(B={"name": "action", "value": "read", "operator": "equal"}, expression="A&&B")
        result, _ = self.call(data, answer)
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["validation_scope"], "structure_and_individual_conditions_only")
        self.assertEqual(result["engine_compatibility"], "not_verified")
        self.assertFalse(result["recommendation"]["automatic_apply"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        result = analysis.analyze_materials(materials(), lambda messages: json.dumps(draft(), ensure_ascii=False))
        record = result["recommendation"]
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": result["status"],
                    "issue": record["issue"],
                    "recommendation": record["recommendation"],
                    "filter": record["filter"],
                    "generation_calls": result["generation_calls"],
                    "validation_scope": result["validation_scope"],
                    "engine_compatibility": result["engine_compatibility"],
                    "model_calls": result["model_calls"],
                    "database_calls": 0,
                    "redis_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
