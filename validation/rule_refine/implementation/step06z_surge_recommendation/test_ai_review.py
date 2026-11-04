"""Offline checks for request isolation and syntax-only AI draft validation."""

import ast
import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location(
    "surge_ai_review_under_test", Path(__file__).with_name("ai_review.py")
)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def rule():
    return {
        "topic": "any-topic",
        "rule_code": "DEMO",
        "rule_name": "人工通用规则",
        "configuration": {"terms": [{"alias": "A", "expression": "A"}]},
    }


def sample(identity="new-1", field="command", value="periodic status"):
    return {
        "alert_id": identity,
        "raw_logs": [{field: value, "port": 123, "process": {"path": "/test"}}],
    }


def answer(status="proposal"):
    return {
        "status": status,
        "issue": "同类业务活动集中触发",
        "recommendation": "建议新增排除条件，运营确认后应用",
        "certainty": "需要确认业务用途",
        "sample_alert_ids": ["new-1"] if status == "proposal" else [],
        "filter": {
            "topic": "any-topic",
            "A": {"name": "command", "operator": "contain", "value": "periodic"},
            "expression": "A",
        }
        if status == "proposal"
        else None,
    }


def parse(value, samples=None, *, phase="generate"):
    return review.parse_reply(
        json.dumps(value, ensure_ascii=False),
        rule=rule(),
        visible_samples=[sample()] if samples is None else samples,
        phase=phase,
    )


class RequestTests(unittest.TestCase):
    def test_independent_assessment_is_separate_prompt_with_no_draft(self):
        current = [sample()]
        request = review.build_request(rule(), current, [], {}, independent=True)
        self.assertEqual(request["prompt_version"], review.INDEPENDENT_PROMPT_VERSION)
        self.assertEqual(
            request["context"]["assessment_mode"], "independent_reassessment"
        )
        self.assertNotIn("draft", request["context"])
        self.assertEqual(request["context"]["current_samples"], current)
        self.assertIn("原文片段", request["messages"][0]["content"])
        self.assertNotIn(review.REVIEW_SYSTEM, request["messages"][0]["content"])
        self.assertIsNone(request["model_output_token_limit"])
        for options in ({"phase": "review", "draft": {}}, {"draft": {}}):
            with (
                self.subTest(options=options),
                self.assertRaisesRegex(
                    ValueError, "independent_assessment_must_not_receive_draft"
                ),
            ):
                review.build_request(
                    rule(), current, [], {}, independent=True, **options
                )

    def test_request_hash_matches_complete_messages_and_does_not_mutate(self):
        source_rule, current, history = rule(), [sample()], [sample("old-1")]
        before = copy.deepcopy((source_rule, current, history))
        request = review.build_request(
            source_rule, current, history, {"available": 2000}
        )
        self.assertEqual((source_rule, current, history), before)
        raw = review.encoded(request["messages"])
        self.assertEqual(request["input_bytes"], len(raw))
        self.assertEqual(request["prompt_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(request["messages"]), 2)
        self.assertEqual(request["messages"][0]["role"], "system")
        self.assertEqual(request["input_token_budget"], 209715)
        self.assertIsNone(request["model_output_token_limit"])

    def test_no_audit_original_answer_or_model_outcome_in_prompt(self):
        r = rule()
        r.update(
            raw_record={"text": "SECRET-RAW-ORIGINAL"},
            original_rule={"expected_filter": "SECRET-OLD"},
        )
        r["configuration"]["audit"] = {"removed_conditions": "SECRET-REMOVED"}
        current = [sample()]
        current[0]["agent_response"] = "SECRET-PRIOR-MODEL"
        current[0]["label"] = "SECRET-LABEL"
        request = review.build_request(
            r,
            current,
            [],
            {
                "available": 2000,
                "expected_filter": "SECRET-ANSWER",
                "nested": {"removed_clauses": "SECRET-AUDIT"},
            },
        )
        self.assertNotIn("SECRET-", review.encoded(request["messages"]).decode())

    def test_full_logs_retained_without_topic_specific_projection(self):
        current = [sample(field="unexpected_vendor_payload", value="X" * 15000)]
        current[0]["raw_logs"][0]["other_arbitrary_field"] = {"nested": [1, 2]}
        request = review.build_request(rule(), current, [], {})
        self.assertEqual(
            request["context"]["current_samples"][0]["raw_logs"], current[0]["raw_logs"]
        )

    def test_field_catalog_uses_exact_real_keys_not_json_string_contents(self):
        current = [sample(field="vendor_command", value="status")]
        current[0]["raw_logs"][0].update(
            message='{"vendor":{"command":"not a field"}}',
            actual_nested={"Command": "status"},
        )
        current[0]["agent_response"] = {"hidden_key": "old answer"}
        request = review.build_request(rule(), current, [], {})
        fields = request["context"]["visible_field_names"]
        self.assertEqual(fields, sorted(set(fields)))
        self.assertIn("vendor_command", fields)
        self.assertIn("actual_nested.Command", fields)
        self.assertNotIn("vendor.command", fields)
        self.assertNotIn("actual_nested.command", fields)
        self.assertNotIn("hidden_key", fields)
        limits = SimpleNamespace(
            input_token_budget=request["estimated_input_tokens"] - 1,
            input_bytes=10**8,
            audit=lambda: {},
        )
        with self.assertRaisesRegex(ValueError, "input_budget_exceeded"):
            review.build_request(rule(), current, [], {}, limits=limits)

    def test_draft_field_issues_are_visible_to_review_without_rewriting_filter(self):
        value = answer()
        value["filter"]["A"]["name"] = "process.command"
        draft = parse(value)
        request = review.build_request(
            rule(), [sample()], [], {}, draft=draft, phase="review"
        )
        self.assertEqual(request["context"]["draft"]["filter"], value["filter"])
        self.assertEqual(
            request["context"]["draft_validation_issues"], draft["validation_issues"]
        )
        draft["validation_issues"][0]["field"] = "changed"
        self.assertEqual(
            request["context"]["draft_validation_issues"][0]["field"], "process.command"
        )

    def test_history_missing_allowed_and_recent_not_limited_to_one_hour(self):
        request = review.build_request(rule(), [sample()], [], {})
        self.assertEqual(request["context"]["history_samples"], [])
        self.assertIn("not_previous_one_hour", request["context"]["history_basis"])
        self.assertIn("历史缺失或不足20条不自动否决", request["messages"][0]["content"])

    def test_material_occurrence_timestamp_remains_visible(self):
        current, history = sample(), sample("old-1")
        current["occurred_at"] = "2026-11-02T10:00:00"
        history["occurred_at"] = "2026-10-30T08:00:00"
        request = review.build_request(rule(), [current], [history], {})
        self.assertEqual(
            request["context"]["history_samples"][0]["occurred_at"],
            history["occurred_at"],
        )
        self.assertEqual(
            request["context"]["current_samples"][0]["occurred_at"],
            current["occurred_at"],
        )

    def test_budget_preflight_permits_empty_samples(self):
        request = review.build_request(rule(), [], [], {})
        self.assertEqual(request["context"]["current_samples"], [])

    def test_review_starts_fresh_with_draft_cited_and_new_originals(self):
        draft = parse(answer())
        draft["private_expected_filter"] = "SECRET-ANSWER"
        current = [sample(), sample("new-2")]
        request = review.build_request(
            rule(),
            current,
            [sample("old-1")],
            {"new_current_samples": 1},
            draft=draft,
            phase="review",
        )
        self.assertEqual(len(request["messages"]), 2)
        self.assertEqual(request["context"]["draft"]["filter"], draft["filter"])
        self.assertTrue(request["context"]["history_reused_from_generation"])
        self.assertNotIn("SECRET-ANSWER", review.encoded(request["messages"]).decode())
        self.assertIn("另外抽取的当前日志", request["messages"][0]["content"])
        self.assertIn("此后不再调用模型", request["messages"][0]["content"])

    def test_review_preserves_model_extras_separately_as_data(self):
        value = answer()
        value["review_verdict"] = {"accepted": False, "reason": "重新检查范围"}
        value["automatic_apply"] = True
        draft = parse(value)
        request = review.build_request(
            rule(), [sample()], [], {}, draft=draft, phase="review"
        )
        extras = request["context"]["draft_model_extra_fields"]
        self.assertEqual(extras, draft["model_extra_fields"])
        self.assertNotIn("automatic_apply", request["context"])
        self.assertNotIn("review_verdict", request["context"]["draft"])
        draft["model_extra_fields"]["review_verdict"]["accepted"] = True
        self.assertFalse(extras["review_verdict"]["accepted"])

    def test_phase_objectives_and_complete_template_use_one_output_contract(self):
        draft = parse(answer())
        generated = review.build_request(rule(), [sample()], [], {})
        reviewed = review.build_request(
            rule(), [sample()], [], {}, draft=draft, phase="review"
        )
        template = json.loads(review.REPLY_TEMPLATE)
        self.assertEqual(set(template), review.REPLY_KEYS)
        self.assertIsInstance(template["sample_alert_ids"], list)
        self.assertEqual(set(template["filter"]), {"topic", "A", "expression"})
        self.assertIn("主要重复场景", generated["messages"][0]["content"])
        review_prompt = reviewed["messages"][0]["content"]
        self.assertIn("保留、收窄或撤回", review_prompt)
        self.assertIn("不能以让草案通过为目标", review_prompt)
        self.assertIn("同一alert_id可能有不同活动", review_prompt)
        self.assertIn("哪一项具体条件区分", review_prompt)
        self.assertIn("不能同时声称存在不同活动和全部属于同类场景", review_prompt)
        self.assertNotEqual(generated["messages"][0], reviewed["messages"][0])
        for request in (generated, reviewed):
            prompt = request["messages"][0]["content"]
            self.assertIn(review.REPLY_TEMPLATE, prompt)
            self.assertIn("逐字保留英文键名", prompt)
            self.assertNotIn("net1", prompt)
            self.assertNotIn("mshta", prompt)
            self.assertEqual(request["prompt_version"], review.PROMPT_VERSION)

    def test_wrong_phase_or_missing_review_draft_rejected(self):
        for kwargs in ({"phase": "other"}, {"phase": "review"}, {"draft": answer()}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                review.build_request(rule(), [sample()], [], {}, **kwargs)

    def test_operator_context_only_explicitly_added(self):
        context = "运营称此次由统一检查任务产生，需复核具体过滤条件。"
        request = review.build_request(
            rule(), [sample()], [], {}, operator_context=context
        )
        self.assertEqual(request["context"]["operator_context"], context)
        self.assertNotIn("net1", review.SYSTEM)

    def test_input_budget_is_explicit_and_no_output_generation_quota(self):
        limits = SimpleNamespace(
            input_token_budget=100,
            input_bytes=10**8,
            audit=lambda: {"operator_assumption": True},
        )
        with self.assertRaisesRegex(ValueError, "^input_budget_exceeded$"):
            review.build_request(rule(), [sample()], [], {}, limits=limits)
        limits.input_token_budget = 800000
        request = review.build_request(rule(), [sample()], [], {}, limits=limits)
        self.assertIsNone(request["model_output_token_limit"])
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("32K", request["messages"][0]["content"])

    def test_structural_error_is_not_token_budget_error(self):
        limits = SimpleNamespace(
            input_token_budget=800000, input_bytes=10, audit=lambda: {}
        )
        with self.assertRaisesRegex(ValueError, "^request_structure_over_budget$"):
            review.build_request(rule(), [sample()], [], {}, limits=limits)


class ReplyTests(unittest.TestCase):
    def test_proposal_valid_without_filter_evaluation(self):
        result = parse(answer())
        self.assertEqual(result["status"], "proposal")
        self.assertTrue(result["recommendation_produced"])
        self.assertFalse(result["program_filter_evaluation_performed"])
        self.assertEqual(result["engine_compatibility"], "not_verified")
        self.assertNotIn("coverage_percent", result)
        self.assertNotIn("evaluation", result)

    def test_top_level_extras_preserved_without_overriding_local_metadata(self):
        value = answer()
        extras = {
            "review_verdict": {"accepted": False, "reason": "条件需调整"},
            "automatic_apply": True,
            "format_valid": False,
            "program_filter_evaluation_performed": True,
            "engine_compatibility": "verified",
        }
        value.update(extras)
        before = copy.deepcopy(value)
        result = parse(value)
        self.assertEqual(result["model_extra_fields"], extras)
        self.assertIn("extra_reply_fields_preserved", result["output_warnings"])
        self.assertTrue(result["format_valid"])
        self.assertFalse(result["program_filter_evaluation_performed"])
        self.assertEqual(result["engine_compatibility"], "not_verified")
        self.assertNotIn("automatic_apply", result)
        result["model_extra_fields"]["review_verdict"]["accepted"] = True
        self.assertEqual(value, before)

    def test_extra_fields_never_replace_required_core_fields_or_validation(self):
        for change, expected_code in (
            (lambda value: value.pop("recommendation"), "missing_reply_field"),
            (lambda value: value.update(status="accepted"), "invalid_status"),
            (
                lambda value: value.update(sample_alert_ids=["unseen"]),
                "invisible_or_duplicate_sample_reference",
            ),
            (
                lambda value: value["filter"]["A"].update(note="extra condition data"),
                "invalid_condition_shape",
            ),
        ):
            value = answer()
            value["explanation"] = "additional model text"
            change(value)
            with (
                self.subTest(code=expected_code),
                self.assertRaises(review.ReplyValidationError) as caught,
            ):
                parse(value)
            self.assertEqual(caught.exception.code, expected_code)

    def test_observed_reference_typo_normalizes_with_audit_and_preserves_text(self):
        value = answer()
        value[review.OBSERVED_REFERENCE_ALIAS] = value.pop("sample_alert_ids")
        value["review_notes"] = "存在另一类活动；仍需人工核对，并非全部相同。"
        before = copy.deepcopy(value)
        result = parse(value)
        self.assertEqual(result["sample_alert_ids"], ["new-1"])
        self.assertEqual(
            result["model_extra_fields"][review.OBSERVED_REFERENCE_ALIAS], ["new-1"]
        )
        self.assertEqual(
            result["output_field_normalizations"],
            [{"from": review.OBSERVED_REFERENCE_ALIAS, "to": "sample_alert_ids"}],
        )
        self.assertIn(
            "observed_sample_reference_alias_normalized", result["output_warnings"]
        )
        self.assertEqual(result["review_notes"], value["review_notes"])
        self.assertFalse(result["program_filter_evaluation_performed"])
        result["sample_alert_ids"].append("mutated")
        self.assertEqual(value, before)
        self.assertEqual(
            result["model_extra_fields"][review.OBSERVED_REFERENCE_ALIAS], ["new-1"]
        )

    def test_observed_reference_typo_does_not_bypass_reference_checks(self):
        for refs, expected in (
            (["not-visible"], "invisible_or_duplicate_sample_reference"),
            (["new-1", "new-1"], "invisible_or_duplicate_sample_reference"),
            ("new-1", "invalid_sample_references"),
            ([123], "invalid_sample_references"),
            ([], "proposal_requires_sample_references"),
        ):
            value = answer()
            value.pop("sample_alert_ids")
            value[review.OBSERVED_REFERENCE_ALIAS] = refs
            with (
                self.subTest(refs=refs),
                self.assertRaises(review.ReplyValidationError) as caught,
            ):
                parse(value)
            self.assertEqual(caught.exception.code, expected)

    def test_observed_reference_typo_never_overrides_conflicting_canonical_field(self):
        value = answer()
        value[review.OBSERVED_REFERENCE_ALIAS] = ["new-2"]
        with self.assertRaises(review.ReplyValidationError) as caught:
            parse(value, [sample(), sample("new-2")])
        self.assertEqual(caught.exception.code, "conflicting_sample_reference_fields")
        value[review.OBSERVED_REFERENCE_ALIAS] = value["sample_alert_ids"][:]
        result = parse(value)
        self.assertEqual(result["output_field_normalizations"], [])
        self.assertIn(
            "duplicate_observed_sample_reference_alias", result["output_warnings"]
        )
        self.assertEqual(result["sample_alert_ids"], ["new-1"])

    def test_other_reference_typos_are_not_fuzzy_mapped(self):
        for alias in ("sample_alert_ids余", "sample_alert_id", "Sample_alert_ids"):
            value = answer()
            value[alias] = value.pop("sample_alert_ids")
            with (
                self.subTest(alias=alias),
                self.assertRaises(review.ReplyValidationError) as caught,
            ):
                parse(value)
            self.assertEqual(
                caught.exception.code, "proposal_requires_sample_references"
            )

    def test_duplicate_keys_and_nonfinite_values_inside_extras_still_rejected(self):
        raw = json.dumps(answer())[:-1]
        for suffix, expected in (
            (',"extra":{"x":1,"x":2}}', "duplicate_json_key"),
            (',"extra":NaN}', "nonfinite_json_value"),
            (',"extra":1e999}', "nonfinite_json_value"),
        ):
            with (
                self.subTest(suffix=suffix),
                self.assertRaises(review.ReplyValidationError) as caught,
            ):
                review.parse_reply(
                    raw + suffix, rule=rule(), visible_samples=[sample()]
                )
            self.assertEqual(caught.exception.code, expected)

    def test_both_reports_display_extra_fields_including_negative_review(self):
        value = answer()
        value["review_verdict"] = {"accepted": False, "reason": "应撤回此草案"}
        value["automatic_apply"] = True
        draft = parse(value)
        namespace = {"json": json}
        summary = {
            "status": "passed",
            "analysis_status": "proposal",
            "current_count": 1,
            "history_count": 0,
            "history_available_count": 0,
            "phases": [],
        }
        for filename in ("runner.py", "saved_generation.py"):
            tree = ast.parse(
                Path(__file__).with_name(filename).read_text(encoding="utf-8")
            )
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "report"
            )
            exec(
                compile(ast.Module(body=[function], type_ignores=[]), filename, "exec"),
                namespace,
            )
            args = (
                (SimpleNamespace(case={"rule": rule()}), summary, draft)
                if filename == "runner.py"
                else (summary, draft)
            )
            body = namespace["report"](*args)
            with self.subTest(filename=filename):
                self.assertIn('"accepted": false', body)
                self.assertIn("应撤回此草案", body)
                self.assertIn('"automatic_apply": true', body)
                self.assertIn("未作自动执行依据", body)

    def test_fenced_json_and_missing_certainty_supported(self):
        value = answer()
        del value["certainty"]
        result = review.parse_reply(
            "```json\n" + json.dumps(value) + "\n```",
            rule=rule(),
            visible_samples=[sample()],
        )
        self.assertEqual(result["certainty_source"], "missing")
        self.assertIn("missing_certainty", result["output_warnings"])

    def test_complete_fence_layouts_preserve_same_json(self):
        raw = json.dumps(answer(), ensure_ascii=False)
        expected = parse(answer())
        for opening, ending in (
            ("```json ", "```"),
            ("``` json\r\n", "\r\n```"),
            ("```JSON\n", "```"),
            ("```json", "```"),
            ("```", "```"),
            ("````json\n", "\n````"),
            ("~~~json\n", "\n~~~"),
            ("```json\n", "\n````"),
        ):
            with self.subTest(opening=opening, ending=ending):
                result = review.parse_reply(
                    opening + raw + ending, rule=rule(), visible_samples=[sample()]
                )
                self.assertEqual(result, expected)

    def test_complete_json_without_closing_fence_keeps_answer_and_warns(self):
        value = answer()
        value["issue"] = "原文\u2028及反引号```均保留"
        raw = json.dumps(value, ensure_ascii=False)
        expected = parse(value)
        expected["output_warnings"] = ["missing_closing_json_fence"]
        for opening, ending in (
            ("```json\n", ""),
            ("``` json ", "\r\n\t "),
            ("````JSON\n", ""),
            ("~~~json\n", ""),
        ):
            with self.subTest(opening=opening):
                self.assertEqual(
                    review.parse_reply(
                        opening + raw + ending,
                        rule=rule(),
                        visible_samples=[sample()],
                    ),
                    expected,
                )

    def test_incomplete_json_or_mixed_fences_do_not_extract_a_partial_answer(self):
        raw = json.dumps(answer())
        for text in (
            "```json\n" + raw[:-1],
            "```json\n" + raw + "%",
            "```json\n" + raw + "\nadditional conclusion",
            "```json\n" + raw + "\n" + raw,
            "````json\n" + raw + "\n```",
            "```json\n" + raw + "\n~~~",
            "```python\n" + raw + "\n```",
            "```json\n" + raw + "\n```\nadditional conclusion",
            "```json\n" + raw + "\n```\n```json\n" + raw + "\n```",
            "```json\n" + raw + raw + "\n```",
            "explanation\n```json\n" + raw + "\n```",
        ):
            with (
                self.subTest(text=text[:25]),
                self.assertRaises(review.ReplyValidationError),
            ):
                review.parse_reply(text, rule=rule(), visible_samples=[sample()])

    def test_fence_markers_inside_json_strings_are_not_delimiters(self):
        value = answer()
        value["issue"] = '代码片段 ```json {"note":"x"} ``` 只属于文本'
        text = "```json" + json.dumps(value, ensure_ascii=False) + "```"
        self.assertEqual(
            review.parse_reply(text, rule=rule(), visible_samples=[sample()]),
            parse(value),
        )

    def test_unicode_line_separators_in_json_values_are_preserved(self):
        value = answer()
        value["issue"] = "a\u2028b\u2029c\u0085d"
        text = "```json\n" + json.dumps(value, ensure_ascii=False) + "\n```"
        self.assertEqual(
            review.parse_reply(text, rule=rule(), visible_samples=[sample()])["issue"],
            value["issue"],
        )

    def test_fence_compatibility_preserves_duplicate_and_finite_checks(self):
        for text, code in (
            ('```json{"status":1,"status":2}```', "duplicate_json_key"),
            ('```json{"value":NaN}```', "nonfinite_json_value"),
            ('```json{"value":1e999}```', "nonfinite_json_value"),
            ('```json{"status":1,"status":2}', "duplicate_json_key"),
            ('```json{"value":NaN}', "nonfinite_json_value"),
            ('```json{"value":1e999}', "nonfinite_json_value"),
        ):
            with (
                self.subTest(code=code),
                self.assertRaises(review.ReplyValidationError) as caught,
            ):
                review.parse_reply(text, rule=rule(), visible_samples=[sample()])
            self.assertEqual(caught.exception.code, code)

    def test_nonproposals_valid_without_historical_samples(self):
        for status in ("no_change", "insufficient_evidence"):
            result = parse(answer(status), [])
            self.assertEqual(result["status"], status)
            self.assertFalse(result["recommendation_produced"])
            self.assertIsNone(result["filter"])

    def test_numeric_and_other_json_values_not_predicate_validated(self):
        for value in (123, "123", [123, 456], {"manual": "value"}, None):
            reply = answer()
            reply["filter"]["A"].update(name="port", operator="equal", value=value)
            self.assertEqual(parse(reply)["filter"]["A"]["value"], value)

    def test_no_value_matching_or_reference_condition_support(self):
        reply = answer()
        reply["filter"]["A"]["value"] = "not present in any source log"
        self.assertTrue(parse(reply)["format_valid"])

    def test_arbitrary_topic_fields_and_exact_nested_path(self):
        for field in ("process.path", "port"):
            reply = answer()
            reply["filter"]["A"]["name"] = field
            self.assertTrue(parse(reply)["format_valid"])

    def test_invented_field_and_case_change_remain_visible_as_revision_issues(self):
        for field in ("invented", "Command", "process.missing", "path"):
            reply = answer()
            reply["filter"]["A"]["name"] = field
            with self.subTest(field=field):
                result = parse(reply)
                self.assertTrue(result["format_valid"])
                self.assertFalse(result["recommendation_ready"])
                self.assertEqual(result["filter"], reply["filter"])
                self.assertEqual(
                    result["validation_issues"],
                    [
                        {
                            "code": "field_not_visible",
                            "path": ["filter", "A", "name"],
                            "field": field,
                        }
                    ],
                )

    def test_all_unknown_filter_fields_recorded_in_stable_order_no_guessing(self):
        value = answer()
        value["filter"]["A"]["name"] = "process.command"
        value["filter"].update(
            B={
                "name": "process.path_wrong",
                "operator": "contain",
                "value": "elsewhere",
            },
            expression="A && B",
        )
        value["review_notes"] = "条件仍需修改，不要把这段原文改成通过。"
        value["recommendation_ready"] = True
        value["validation_issues"] = []
        before = copy.deepcopy(value)
        result = parse(value)
        self.assertEqual(
            [item["path"] for item in result["validation_issues"]],
            [
                ["filter", "A", "name"],
                ["filter", "B", "name"],
            ],
        )
        for key in ("status", "filter", "issue", "recommendation", "review_notes"):
            self.assertEqual(result[key], value[key])
        self.assertEqual(value, before)
        self.assertFalse(result["recommendation_ready"])
        self.assertTrue(result["model_extra_fields"]["recommendation_ready"])
        self.assertFalse(result["program_filter_evaluation_performed"])

    def test_valid_field_after_revision_clears_only_field_issue_not_claiming_semantics(
        self,
    ):
        value = answer()
        result = parse(value)
        self.assertTrue(result["recommendation_ready"])
        self.assertEqual(result["validation_issues"], [])
        self.assertEqual(result["engine_compatibility"], "not_verified")

    def test_invisible_and_duplicate_reference_rejected(self):
        for refs in (["old-unseen"], ["new-1", "new-1"]):
            reply = answer()
            reply["sample_alert_ids"] = refs
            with self.assertRaises(review.ReplyValidationError):
                parse(reply)

    def test_history_reference_is_valid_when_original_visible(self):
        reply = answer()
        reply["sample_alert_ids"] = ["old-1"]
        result = parse(reply, [sample(), sample("old-1")])
        self.assertEqual(result["sample_alert_ids"], ["old-1"])

    def test_valid_expression_shape_without_execution(self):
        reply = answer()
        reply["filter"].update(
            B={"name": "port", "operator": "equal", "value": "9"},
            expression=" (A || B) && A ",
        )
        self.assertTrue(parse(reply)["format_valid"])

    def test_unknown_expression_syntax_not_silently_accepted(self):
        for expression in (
            "A &&",
            "A AND A",
            "!A",
            "A;system()",
            "A B",
            "(A",
            "A)",
            "A || C",
            "()",
            "A(B)",
        ):
            reply = answer()
            reply["filter"]["expression"] = expression
            with (
                self.subTest(expression=expression),
                self.assertRaises(review.ReplyValidationError),
            ):
                parse(reply)

    def test_unknown_operator_rejected_with_advice_path_available(self):
        for operator in ("regex", "contains", ["equal"]):
            reply = answer()
            reply["filter"]["A"]["operator"] = operator
            with self.assertRaises(review.ReplyValidationError) as caught:
                parse(reply)
            self.assertEqual(caught.exception.code, "unsupported_condition_operator")
        reply = answer("no_change")
        reply["review_notes"] = "需要正则条件，建议运营人工处理。"
        self.assertTrue(parse(reply)["format_valid"])

    def test_sample_request_only_visible_field_without_value_match(self):
        reply = answer()
        reply["sample_request"] = {"field": "command", "text": "some new combination"}
        self.assertEqual(parse(reply)["sample_request"], reply["sample_request"])
        reply["sample_request"]["field"] = "unknown"
        result = parse(reply)
        self.assertIsNone(result["sample_request"])
        self.assertEqual(
            result["ignored_sample_request"],
            {
                "value": reply["sample_request"],
                "reason": "field_not_visible",
            },
        )
        self.assertTrue(result["recommendation_ready"])
        self.assertIn("invalid_sample_request_ignored", result["output_warnings"])

    def test_sample_request_bad_shapes_warn_without_discarding_core_suggestion(self):
        for request in (
            {"field": "command"},
            {"field": "command", "text": ""},
            {"field": "command", "text": "x", "execute": True},
            [],
        ):
            reply = answer()
            reply["sample_request"] = request
            with self.subTest(request=request):
                result = parse(reply)
                self.assertIsNone(result["sample_request"])
                self.assertEqual(result["ignored_sample_request"]["value"], request)
                self.assertIn(
                    "invalid_sample_request_ignored", result["output_warnings"]
                )
                self.assertEqual(result["filter"], reply["filter"])
                self.assertTrue(result["recommendation_ready"])

    def test_review_does_not_act_on_any_optional_sample_request(self):
        for request in (
            {"field": "command", "text": "periodic"},
            {"field": "unknown", "text": "x"},
            [],
        ):
            reply = answer()
            reply["sample_request"] = request
            result = parse(reply, phase="review")
            self.assertIsNone(result["sample_request"])
            self.assertEqual(
                result["ignored_sample_request"],
                {
                    "value": request,
                    "reason": "review_complete_no_further_sampling",
                },
            )
            self.assertIn("review_sample_request_ignored", result["output_warnings"])
            self.assertTrue(result["recommendation_ready"])

    def test_unknown_reply_phase_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown_review_phase"):
            parse(answer(), phase="unknown")

    def test_filter_missing_wrong_topic_and_nonproposal_filter_rejected(self):
        variants = []
        value = answer()
        value["filter"]["topic"] = "other"
        variants.append(value)
        value = answer()
        value["filter"] = None
        variants.append(value)
        value = answer()
        value["sample_alert_ids"] = []
        variants.append(value)
        value = answer()
        value["status"] = "no_change"
        variants.append(value)
        for value in variants:
            with self.assertRaises(review.ReplyValidationError):
                parse(value)

    def test_malformed_json_duplicate_keys_and_nonfinite_numbers_rejected(self):
        for text in (
            '{"status":"proposal","status":"no_change"}',
            '{"bad":NaN}',
            '{"bad":1e999}',
            "[]",
            "prefix {}",
            "```python\n{}\n```",
            "```json\n{}\n``` trailing",
        ):
            with (
                self.subTest(text=text),
                self.assertRaises(review.ReplyValidationError),
            ):
                review.parse_reply(text, rule=rule(), visible_samples=[sample()])

    def test_output_may_exceed_old_64k_without_receiver_quota(self):
        reply = answer("no_change")
        reply["issue"] = "大" * 30000
        self.assertEqual(parse(reply)["issue"], reply["issue"])

    def test_sample_and_response_objects_unchanged(self):
        reply, samples = answer(), [sample()]
        before = copy.deepcopy((reply, samples))
        parse(reply, samples)
        self.assertEqual((reply, samples), before)

    def test_schema_type_errors_have_structured_codes(self):
        for field, value in (
            ("status", []),
            ("issue", {}),
            ("certainty", 1),
            ("sample_alert_ids", [None]),
            ("review_notes", []),
        ):
            reply = answer()
            reply[field] = value
            with (
                self.subTest(field=field),
                self.assertRaises(review.ReplyValidationError),
            ):
                parse(reply)


if __name__ == "__main__":
    unittest.main(verbosity=2)
