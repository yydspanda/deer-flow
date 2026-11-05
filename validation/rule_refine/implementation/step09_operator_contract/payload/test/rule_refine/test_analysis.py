"""规则建议生成边界测试；使用人工回复，不连接模型或外部服务。"""

import copy
import importlib
import importlib.util
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

fixture_path = Path(__file__).with_name("test_materials.py")
spec = importlib.util.spec_from_file_location(
    "_rule_refine_analysis_fixtures", fixture_path
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
importlib.import_module(fixtures.package.__name__ + ".condition_values")
analysis = importlib.import_module(fixtures.package.__name__ + ".analysis")
contracts = importlib.import_module(fixtures.package.__name__ + ".contracts")


def materials(rows=None):
    rows = [fixtures.row()] if rows is None else rows
    provider = fixtures.rules()
    provider.get_rule.return_value["configuration"] = {"terms": [{"expression": "A"}]}
    ids = list(dict.fromkeys(item["alert_id"] for item in rows))
    return fixtures.build(
        fixtures.signal(ids), provider, rows, source_kind="synthetic_fixture"
    )


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
            "A": {"name": "host", "value": "synthetic.invalid", "operator": "Eq"},
        },
    }


class AnalysisTests(unittest.TestCase):
    def call(self, data=None, answer=None):
        generate = Mock(
            return_value=json.dumps(
                draft() if answer is None else answer, ensure_ascii=False
            )
        )
        result = analysis.analyze_materials(
            materials() if data is None else data, generate
        )
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
        self.assertEqual(
            (record.company_code, record.rule_code), ("demo-company", "DEMO_RULE")
        )
        self.assertEqual(record.rule_config_version, "demo-config-v1")
        self.assertEqual(record.evidence.alert_count, 1000)
        self.assertEqual(record.evidence.sample_alert_ids, ["101"])
        self.assertFalse(record.automatic_apply)
        self.assertEqual(
            result["validation_scope"], "structure_and_individual_conditions_only"
        )
        self.assertEqual(result["engine_compatibility"], "not_verified")
        self.assertEqual((result["generation_calls"], result["model_calls"]), (1, 0))
        generate.assert_called_once_with(
            analysis.build_analysis_request(data)["messages"]
        )

    def test_request_is_stable_bounded_and_does_not_mutate_materials(self):
        data = materials()
        before = copy.deepcopy(data)
        first = analysis.build_analysis_request(data)
        self.assertEqual(first, analysis.build_analysis_request(data))
        self.assertEqual(len(first["prompt_sha256"]), 64)
        self.assertEqual(
            [item["role"] for item in first["messages"]], ["system", "user"]
        )
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
                self.assertEqual(
                    (result["generation_calls"], result["model_calls"]), (0, 0)
                )
                generate.assert_not_called()

    def test_nonproposal_states_do_not_emit_a_recommendation(self):
        for status in (
            "no_change",
            "insufficient_evidence",
            "needs_existing_condition_change",
        ):
            answer = {
                **draft(),
                "status": status,
                "sample_alert_ids": [],
                "filter": None,
            }
            result, generate = self.call(answer=answer)
            self.assertEqual(result["status"], status)
            self.assertIsNone(result["recommendation"])
            generate.assert_called_once()

    def test_unknown_fields_ids_and_topic_are_rejected(self):
        for update in (
            {"company_code": "forged"},
            {"sample_alert_ids": ["expired-999"]},
        ):
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
        duplicate = normal.replace(
            '"status": "proposal"', '"status": "no_change", "status": "proposal"'
        )
        nested_duplicate = normal.replace(
            '"name": "host"', '"name": "wrong", "name": "host"'
        )
        for reply in ("", "{", "[]", duplicate, nested_duplicate, "x" * 100000):
            with self.subTest(length=len(reply)):
                generate = Mock(return_value=reply)
                result = analysis.analyze_materials(materials(), generate)
                self.assert_failed(result, generate)

    def test_generation_failure_is_not_reported_as_no_change(self):
        generate = Mock(side_effect=RuntimeError("人工注入错误"))
        self.assert_failed(analysis.analyze_materials(materials(), generate), generate)

    def test_complete_single_json_code_fences_are_accepted(self):
        raw = json.dumps(draft(), ensure_ascii=False)
        data = materials()
        for label in ("json", "JSON", ""):
            for separator in ("\n", "\r\n"):
                wrapped = (
                    " \t\r\n```" + label + separator + raw + separator + "``` \t\r\n"
                )
                with self.subTest(label=label, separator=separator):
                    generate = Mock(return_value=wrapped)
                    result = analysis.analyze_materials(data, generate)
                    self.assertEqual(result["status"], "proposal")
                    self.assertEqual(
                        result["response_sha256"],
                        hashlib.sha256(wrapped.encode("utf-8")).hexdigest(),
                    )
                    self.assertEqual(
                        result["prompt_sha256"],
                        analysis.build_analysis_request(data)["prompt_sha256"],
                    )
                    self.assertEqual(result["model_calls"], 0)
                    generate.assert_called_once()

    def test_fences_never_extract_json_from_mixed_or_incomplete_text(self):
        raw = json.dumps(draft())
        fenced = "```json\n" + raw + "\n```"
        cases = (
            "说明\n" + fenced,
            fenced + "\n说明",
            fenced + "\n" + fenced,
            "```json\n" + raw,
            "```json " + raw + "```",
            "```python\n" + raw + "\n```",
            "````json\n" + raw + "\n````",
            "\ufeff" + fenced,
            "\u00a0" + fenced,
            "```json\n" + raw + "\n```\n说明\n```",
        )
        for reply in cases:
            with self.subTest(reply=reply[:30]):
                generate = Mock(return_value=reply)
                self.assert_failed(
                    analysis.analyze_materials(materials(), generate), generate
                )

    def test_wrapper_does_not_change_json_string_values(self):
        answer = draft()
        answer["issue"] = "人工说明：保留```literal```、中文\u2028和\u2029分隔符"
        raw = json.dumps(answer, ensure_ascii=False)
        wrapped = "```json\n" + raw + "\n```"
        self.assertEqual(analysis._json_document(wrapped), raw)
        result = analysis.analyze_materials(materials(), Mock(return_value=wrapped))
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["issue"], answer["issue"])

    def test_wrapped_output_still_enforces_json_contract_and_grounding(self):
        raw = json.dumps(draft())
        unknown = draft()
        unknown["sample_alert_ids"] = ["not-shown"]
        unsupported = draft()
        unsupported["filter"]["A"]["value"] = "not-in-log"
        malformed = draft()
        malformed.pop("certainty")
        replies = (
            raw.replace(
                '"status": "proposal"', '"status": "no_change", "status": "proposal"'
            ),
            raw.replace('"name": "host"', '"name": "wrong", "name": "host"'),
            raw.replace('"certainty":', '"extra": NaN, "certainty":'),
            json.dumps(unknown),
            json.dumps(unsupported),
            json.dumps(malformed),
            "[]",
            "null",
            "true",
            "{} {}",
        )
        for reply in replies:
            with self.subTest(reply=reply[:40]):
                generate = Mock(return_value="```json\n" + reply + "\n```")
                self.assert_failed(
                    analysis.analyze_materials(materials(), generate), generate
                )

    def test_original_size_limit_counts_the_wrapper_before_unwrapping(self):
        answer = {
            **draft(),
            "status": "no_change",
            "sample_alert_ids": [],
            "filter": None,
        }
        raw = json.dumps(answer, ensure_ascii=False)
        limit = analysis.get_limits().output_bytes
        raw += " " * (limit - 4 - len(raw.encode("utf-8")))
        self.assertEqual(len(raw.encode("utf-8")), limit - 4)
        plain = analysis.analyze_materials(materials(), Mock(return_value=raw))
        self.assertEqual(plain["status"], "no_change")
        generate = Mock(return_value="```json\n" + raw + "\n```")
        self.assert_failed(analysis.analyze_materials(materials(), generate), generate)

    def test_supported_operators_preserve_the_existing_contract(self):
        for operator, value in (
            ("Eq", "synthetic.invalid"),
            ("Contains", "synthetic."),
            ("NotContains", "malware"),
        ):
            answer = draft()
            answer["filter"]["A"].update(operator=operator, value=value)
            result, _ = self.call(answer=answer)
            self.assertEqual(result["status"], "proposal")
            self.assertEqual(
                result["recommendation"]["filter"]["A"]["operator"], operator
            )

    def test_missing_field_in_referenced_sample_cannot_prove_notcontain(self):
        other = fixtures.row("102", "task-b")
        other["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"][0]["action"] = (
            "allowed"
        )
        answer = draft()
        answer["filter"]["A"].update(
            name="action", operator="NotContains", value="blocked"
        )
        self.assert_failed(*self.call(materials([fixtures.row(), other]), answer))

    def test_omitted_whole_log_cannot_ground_a_condition_from_hidden_values(self):
        data = materials()
        data["samples"][0]["raw_logs"].append(
            {"host": "hidden.invalid", "large": "x" * (200 * 1024)}
        )
        request = analysis.build_analysis_request(data)
        self.assertGreaterEqual(request["omitted_logs"], 1)
        self.assertNotIn("hidden.invalid", request["messages"][1]["content"])
        answer = draft()
        answer["filter"]["A"]["value"] = "hidden.invalid"
        self.assert_failed(*self.call(data, answer))

    def test_unprojected_sample_reference_is_rejected(self):
        data = materials([fixtures.row(), fixtures.row("102", "task-b")])
        data["samples"][1]["raw_logs"] = [
            {"host": "hidden.invalid", "large": "x" * (200 * 1024)}
        ]
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
            self.assertEqual(
                (result["status"], result["error_code"]),
                ("analysis_failed", "invalid_materials"),
            )
            self.assertEqual(result["generation_calls"], 0)
            generate.assert_not_called()

    def test_separate_condition_support_is_not_reported_as_combined_engine_match(self):
        data = materials()
        data["samples"][0]["raw_logs"] = [
            {"host": "synthetic.invalid"},
            {"action": "read"},
        ]
        answer = draft()
        answer["filter"].update(
            B={"name": "action", "value": "read", "operator": "Eq"},
            expression="A&&B",
        )
        result, _ = self.call(data, answer)
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(
            result["validation_scope"], "structure_and_individual_conditions_only"
        )
        self.assertEqual(result["engine_compatibility"], "not_verified")
        self.assertFalse(result["recommendation"]["automatic_apply"])

    def test_numeric_equal_uses_the_shared_comparator_with_string_target(self):
        data = materials()
        data["samples"][0]["raw_logs"] = [{"port": 443}]
        answer = draft()
        answer["filter"]["A"].update(name="port", value="443", operator="Eq")
        result, generate = self.call(data, answer)
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["recommendation"]["filter"]["A"]["value"], "443")
        generate.assert_called_once()
        for value in ("444", "0443", " 443", "+443"):
            with self.subTest(value=value):
                answer["filter"]["A"]["value"] = value
                self.assert_failed(*self.call(data, answer))

    def test_nonstring_negative_conditions_never_gain_support(self):
        for value in (443, True, None, [], {}, ["safe"]):
            with self.subTest(value_type=type(value).__name__):
                data = materials()
                data["samples"][0]["raw_logs"] = [{"field": value}]
                answer = draft()
                answer["filter"]["A"].update(
                    name="field", value="attack", operator="NotContains"
                )
                result, generate = self.call(data, answer)
                if value is None:
                    self.assert_failed(result, generate)
                else:
                    self.assertEqual(result["status"], "manual_review")
                    self.assertIsNone(result["recommendation"])
                    self.assertFalse(result["automatic_apply"])
                    self.assertEqual(
                        result["review_reason"], "local_evaluation_not_supported"
                    )
        data = materials()
        data["samples"][0]["raw_logs"] = [{"port": "01"}]
        answer = draft()
        answer["filter"]["A"].update(name="port", value="1", operator="Eq")
        self.assert_failed(*self.call(data, answer))

    def test_one_hundred_kib_log_is_retained_whole_and_inputs_are_unchanged(self):
        data = materials()
        log = {"host": "synthetic.invalid", "large": "x" * (100 * 1024)}
        data["samples"][0]["raw_logs"] = [log]
        before = copy.deepcopy(data)
        request = analysis.build_analysis_request(data)
        self.assertEqual(request["context"]["samples"][0]["raw_logs"], [log])
        self.assertEqual(request["omitted_logs"], 0)
        self.assertEqual(request["context"]["invalid_logs"], 0)
        self.assertEqual(request["context"]["budget_omitted_logs"], 0)
        self.assertLessEqual(request["input_bytes"], 128 * 1024)
        self.assertEqual(request["max_input_kib"], 128)
        result, _ = self.call(data)
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(data, before)

    def test_twenty_large_legal_alerts_are_selected_within_input_budget(self):
        rows = []
        for index in range(20):
            row = fixtures.row(str(101 + index), "large-task-" + str(index))
            row["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"][0]["body"] = "x" * (
                110 * 1024
            )
            rows.append(row)
        data = materials(rows)
        self.assertEqual(len(data["samples"]), 20)
        self.assertGreater(
            len(analysis._encode(data, analysis.MAX_MATERIALS_BYTES)), 2 * 1024 * 1024
        )
        request = analysis.build_analysis_request(data)
        self.assertIsNotNone(request)
        self.assertGreater(len(request["context"]["samples"]), 0)
        self.assertLess(len(request["context"]["samples"]), 20)
        self.assertGreater(request["context"]["budget_omitted_logs"], 0)
        self.assertEqual(request["context"]["invalid_logs"], 0)
        self.assertLessEqual(request["input_bytes"], 128 * 1024)
        result, generate = self.call(data)
        self.assertEqual(result["status"], "proposal")
        generate.assert_called_once()

    def test_total_material_reception_limit_rejects_before_model_call(self):
        self.assertEqual(analysis.MAX_MATERIALS_BYTES, 32 * 1024 * 1024)
        data = materials()
        data["samples"][0]["raw_logs"][0]["oversized"] = "x" * (
            analysis.MAX_MATERIALS_BYTES + 1
        )
        with self.assertRaises(ValueError):
            analysis.build_analysis_request(data)
        result, generate = self.call(data)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "invalid_materials")
        self.assertEqual(result["generation_calls"], 0)
        generate.assert_not_called()

    def test_complete_input_budget_includes_system_and_json_escaping(self):
        data = materials()
        log = {"host": "synthetic.invalid", "escaped": "\\" * 20000}
        data["samples"][0]["raw_logs"] = [log, copy.deepcopy(log)]
        before = copy.deepcopy(data)
        request = analysis.build_analysis_request(data)
        self.assertEqual(request["context"]["samples"][0]["raw_logs"], [log])
        self.assertEqual(request["context"]["budget_omitted_logs"], 1)
        self.assertEqual(request["context"]["invalid_logs"], 0)
        encoded = analysis._encode(request["messages"], 256 * 1024)
        self.assertEqual(request["input_bytes"], len(encoded))
        self.assertLessEqual(len(encoded), 128 * 1024)
        self.assertEqual(request["prompt_sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertGreater(
            len(encoded), len(request["messages"][1]["content"].encode("utf-8"))
        )
        self.assertEqual(data, before)

    def test_invalid_budget_and_selection_omissions_are_distinct_and_hashed(self):
        data = materials()
        base = analysis.build_analysis_request(data)
        normal = {"host": "synthetic.invalid"}
        data["samples"][0]["raw_logs"] = [
            {},
            {"large": "x" * (200 * 1024)},
            normal,
            normal,
            normal,
            normal,
        ]
        request = analysis.build_analysis_request(data)
        context = request["context"]
        self.assertEqual(context["invalid_logs"], 1)
        self.assertEqual(context["budget_omitted_logs"], 1)
        self.assertEqual(context["selection_omitted_logs"], 1)
        self.assertEqual(context["omitted_logs"], 3)
        self.assertEqual(len(context["samples"][0]["raw_logs"]), 3)
        self.assertNotEqual(base["prompt_sha256"], request["prompt_sha256"])
        self.assertEqual(json.loads(request["messages"][1]["content"]), context)
        self.assertEqual(
            request["prompt_sha256"],
            hashlib.sha256(
                analysis._encode(request["messages"], 256 * 1024)
            ).hexdigest(),
        )

    def test_new_prompt_version_describes_numeric_contract_and_input_metadata(self):
        request = analysis.build_analysis_request(materials())
        self.assertNotEqual(analysis.PROMPT_VERSION, "rule-refine-analysis-v1")
        self.assertIn("数字字段仅支持Eq和Ne", analysis.SYSTEM)
        self.assertIn("filter.value仍为字符串", analysis.SYSTEM)
        self.assertIn(
            "Contains、NotContains、StartsWith、EndsWith仅适用于字符串", analysis.SYSTEM
        )
        result, _ = self.call()
        self.assertEqual(result["input_bytes"], request["input_bytes"])
        self.assertEqual(
            result["input_size_basis"], "serialized_messages_utf8_json_bytes"
        )
        self.assertEqual(result["max_input_kib"], 128)

    def test_rule_configuration_over_32_kib_fits_final_request_budget(self):
        for size in (33, 62):
            with self.subTest(kib=size):
                data = materials()
                data["rule"]["configuration"]["long_configuration"] = "x" * (
                    size * 1024
                )
                request = analysis.build_analysis_request(data)
                self.assertIsNotNone(request)
                self.assertLessEqual(request["input_bytes"], 128 * 1024)
                self.assertEqual(
                    request["context"]["rule"]["configuration"],
                    data["rule"]["configuration"],
                )
                self.assertEqual(self.call(data)[0]["status"], "proposal")

    def test_old_three_kib_and_hundred_kib_responses_are_not_prematurely_discarded(
        self,
    ):
        for size in (3, 100):
            with self.subTest(kib=size):
                data = materials()
                response = {"previous_analysis": "x" * (size * 1024)}
                data["samples"][0]["stored_response"] = response
                request = analysis.build_analysis_request(data)
                self.assertEqual(
                    request["context"]["samples"][0]["unverified_stored_response"],
                    response,
                )
                self.assertEqual(request["context"]["omitted_responses"], 0)
                self.assertLessEqual(request["input_bytes"], 128 * 1024)

    def test_raw_evidence_is_selected_before_optional_previous_answers(self):
        data = materials([fixtures.row("101", "a"), fixtures.row("102", "b")])
        data["samples"][0]["stored_response"] = "x" * (110 * 1024)
        data["samples"][1]["raw_logs"][0]["body"] = "x" * (80 * 1024)
        request = analysis.build_analysis_request(data)
        self.assertEqual(
            [sample["alert_id"] for sample in request["context"]["samples"]],
            ["101", "102"],
        )
        self.assertNotIn("unverified_stored_response", request["context"]["samples"][0])
        self.assertEqual(request["context"]["omitted_responses"], 1)
        self.assertEqual(request["omitted_logs"], 0)

    def test_contract_valid_37_kib_chinese_answer_is_accepted(self):
        answer = draft()
        for name in ("issue", "recommendation", "certainty"):
            answer[name] = "中" * 4096
        raw = json.dumps(answer, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(raw), 16 * 1024)
        self.assertLess(len(raw), 64 * 1024)
        result, _ = self.call(answer=answer)
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["max_output_kib"], 64)

    def test_oversized_answer_has_specific_reason_and_keeps_request_provenance(self):
        result = analysis.analyze_materials(
            materials(), Mock(return_value=" " * (64 * 1024 + 1))
        )
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "response_over_budget")
        self.assertEqual(result["validation_stage"], "response_budget")
        self.assertIn("prompt_sha256", result)

    def test_unused_alias_is_not_evidence_or_an_executable_condition(self):
        for operator in ("Eq", "Contains"):
            answer = draft()
            answer["filter"]["B"] = {
                "name": "missing",
                "operator": operator,
                "value": "unused",
            }
            before = copy.deepcopy(answer)
            result, _ = self.call(answer=answer)
            self.assertEqual(result["status"], "proposal")
            self.assertNotIn("B", result["recommendation"]["filter"])
            self.assertEqual(answer, before)

    def test_unsupported_numeric_operator_is_rejected_before_evaluation(self):
        data = materials()
        data["samples"][0]["raw_logs"][0]["port"] = 443
        answer = draft()
        answer["filter"]["A"] = {
            "name": "port",
            "operator": "greater_than",
            "value": "100",
        }
        result, _ = self.call(data, answer)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "invalid_output_contract")
        self.assertIsNone(result["recommendation"])
        self.assertFalse(result["automatic_apply"])

    def test_nested_path_is_manual_review_but_missing_path_is_failure(self):
        data = materials()
        data["samples"][0]["raw_logs"] = [{"http": {"host": "synthetic.invalid"}}]
        answer = draft()
        answer["filter"]["A"]["name"] = "http.host"
        result, _ = self.call(data, answer)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(
            result["unsupported_features"][0]["reason"], "nested_field_not_evaluated"
        )
        answer["filter"]["A"]["name"] = "http.nonexistent"
        result, _ = self.call(data, answer)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "condition_field_missing")

    def test_unknown_operator_does_not_bypass_missing_field_or_false_known_condition(
        self,
    ):
        answer = draft()
        answer["filter"]["A"].update(name="absent", operator="regex")
        result, _ = self.call(answer=answer)
        self.assertEqual(result["error_code"], "invalid_output_contract")
        answer = draft()
        answer["filter"]["A"]["value"] = "not-in-data"
        answer["filter"]["B"] = {
            "name": "host",
            "operator": "regex",
            "value": "pattern",
        }
        answer["filter"]["expression"] = "A && B"
        result, _ = self.call(answer=answer)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "invalid_output_contract")

    def test_long_rule_name_is_validated_before_request_and_failure_is_not_model_error(
        self,
    ):
        data = materials()
        data["rule"]["rule_name"] = "规" * 513
        self.assertEqual(self.call(data)[0]["status"], "proposal")
        data["rule"]["rule_name"] = "规" * 4097
        result, generate = self.call(data)
        self.assertEqual(result["error_code"], "invalid_rule_identity")
        self.assertEqual(result["validation_stage"], "materials")
        generate.assert_not_called()
        data["rule"]["rule_name"] = " leading-space"
        result, generate = self.call(data)
        self.assertEqual(result["error_code"], "invalid_rule_identity")
        generate.assert_not_called()

    def test_bad_limit_settings_are_reported_before_model_call(self):
        generate = Mock()
        with patch.object(
            analysis, "get_limits", side_effect=ValueError("bad setting")
        ):
            result = analysis.analyze_materials(materials(), generate)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "invalid_limits_configuration")
        self.assertEqual(result["validation_stage"], "configuration")
        generate.assert_not_called()

    def test_configured_request_and_response_budgets_are_in_prompt_hash(self):
        limits_module = importlib.import_module(fixtures.package.__name__ + ".limits")
        data = materials()
        default = analysis.build_analysis_request(data)
        for settings in (
            limits_module.Limits(input_kib=256),
            limits_module.Limits(output_kib=128),
        ):
            with patch.object(analysis, "get_limits", return_value=settings):
                updated = analysis.build_analysis_request(data)
                self.assertNotEqual(updated["prompt_sha256"], default["prompt_sha256"])
                self.assertEqual(
                    updated["context"]["max_input_kib"], settings.input_kib
                )
                self.assertEqual(
                    updated["context"]["max_output_kib"], settings.output_kib
                )

    def test_failure_stages_do_not_collapse_json_contract_and_evidence_errors(self):
        invalid = [
            ("not-json", "invalid_json", "json"),
            (
                json.dumps({**draft(), "extra": True}),
                "invalid_output_contract",
                "output_contract",
            ),
            (
                json.dumps({**draft(), "sample_alert_ids": ["not-shown"]}),
                "uncited_sample",
                "evidence",
            ),
        ]
        for raw, code, stage in invalid:
            with self.subTest(code=code):
                result = analysis.analyze_materials(materials(), Mock(return_value=raw))
                self.assertEqual(
                    (result["error_code"], result["validation_stage"]), (code, stage)
                )

    def test_many_small_fields_do_not_hit_a_hidden_aggregate_node_cap(self):
        rows = []
        for index in range(20):
            row = fixtures.row(str(101 + index), "many-" + str(index))
            row["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"][0].update(
                {"f" + str(number): "x" for number in range(300)}
            )
            rows.append(row)
        data = materials(rows)
        result, generate = self.call(data)
        self.assertEqual(result["status"], "proposal")
        self.assertLess(result["input_bytes"], 128 * 1024)
        generate.assert_called_once()


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        result = analysis.analyze_materials(
            materials(), lambda messages: json.dumps(draft(), ensure_ascii=False)
        )
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
