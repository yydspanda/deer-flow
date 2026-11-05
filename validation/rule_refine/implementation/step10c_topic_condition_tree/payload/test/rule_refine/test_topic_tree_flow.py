"""Versioned topic condition-tree acceptance with synthetic transports only."""

import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    "_topic_tree_flow_fixtures", Path(__file__).with_name("test_topic_processor.py")
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
ai = importlib.import_module(fixtures.PACKAGE + ".topic_tree_ai")


def tree_reply(request):
    result = fixtures.reply(request)
    result["filter"] = {
        "topic": "synthetic-topic",
        "match": {
            "name": "action",
            "operator": "Eq",
            "value": "inventory-versions",
            "field_role": "event_field",
            "basis": "Synthetic original records action",
        },
    }
    result["topic_review"].pop("field_roles")
    return result


def request(phase="generate", draft=None):
    case = fixtures.case()
    return ai.build_request(
        case["rule"],
        case["current_samples"],
        case["history_samples"],
        case["coverage"],
        phase=phase,
        draft=draft,
    )


class TreeAITests(unittest.TestCase):
    def setUp(self):
        self.request = request()
        self.reply = tree_reply(self.request)

    def parse(self, answer=None, req=None):
        return ai.parse_reply(
            json.dumps(answer or self.reply), request=req or self.request
        )

    def test_new_request_and_response_explicit_contract(self):
        self.assertEqual(self.request["topic_output_contract_version"], ai.CONTRACT)
        generated = self.parse()
        self.assertEqual(generated["filter"]["expression"], "A")
        self.assertNotIn("match", generated["filter"])
        self.assertFalse(generated["topic_submission_ready"])
        self.assertEqual(generated["condition_tree"], self.reply["filter"])
        reviewed = self.parse(req=request("review", generated))
        self.assertTrue(reviewed["topic_submission_ready"])
        self.assertEqual(reviewed["topic_review"]["field_roles"][0]["name"], "action")
        self.assertEqual(reviewed["tree_compilation"]["contract_version"], ai.CONTRACT)

    def test_new_contract_rejects_flat_answer_instead_of_falling_back(self):
        with self.assertRaises(ValueError):
            self.parse(fixtures.reply(self.request))

    def test_old_request_not_silently_upgraded(self):
        old = fixtures.ai.build_request(
            fixtures.rule(), fixtures.case()["current_samples"], [], {}
        )
        with self.assertRaisesRegex(ValueError, "topic_tree_request_contract_required"):
            self.parse(req=old)
        accepted = fixtures.ai.parse_reply(json.dumps(fixtures.reply(old)), request=old)
        self.assertEqual(accepted["filter"]["expression"], "A")

    def test_review_only_receives_predicates_and_grouping_from_generation(self):
        self.reply["filter"]["match"]["basis"] = "GENERATION-BASIS-DO-NOT-PASS"
        self.reply["recommendation"] = "GENERATION-PROSE-DO-NOT-PASS"
        generated = self.parse()
        second = request("review", generated)
        draft = second["context"]["draft"]
        self.assertEqual(draft["filter"]["match"]["name"], "action")
        self.assertNotIn("field_role", draft["filter"]["match"])
        self.assertNotIn("basis", draft["filter"]["match"])
        self.assertNotIn("GENERATION-BASIS-DO-NOT-PASS", json.dumps(second))
        self.assertNotIn("GENERATION-PROSE-DO-NOT-PASS", json.dumps(second))

    def test_changed_compiled_draft_rejected(self):
        generated = self.parse()
        generated["filter"]["A"]["value"] = "unreviewed-change"
        with self.assertRaisesRegex(ValueError, "topic_tree_draft_binding_mismatch"):
            request("review", generated)

    def test_duplicated_parallel_metadata_is_rejected(self):
        self.reply["topic_review"]["field_roles"] = []
        with self.assertRaises(ValueError):
            self.parse()

    def test_unknown_event_field_still_withheld(self):
        self.reply["filter"]["match"]["name"] = "unknown-source-field"
        generated = self.parse()
        reviewed = self.parse(req=request("review", generated))
        self.assertFalse(reviewed["topic_submission_ready"])
        self.assertTrue(reviewed["validation_issues"])

    def test_detection_name_cannot_be_relabelled_as_event_field(self):
        case = fixtures.case()
        case["rule"]["topic"] = "security_qthids"
        for sample in case["current_samples"]:
            sample["raw_logs"][0]["detail_hit_rule_name"] = "Synthetic detector"

        def build(phase, draft=None):
            return ai.build_request(
                case["rule"],
                case["current_samples"],
                [],
                case["coverage"],
                phase=phase,
                draft=draft,
            )

        first = build("generate")
        answer = tree_reply(first)
        answer["filter"]["topic"] = "security_qthids"
        answer["filter"]["match"].update(
            name="detail_hit_rule_name",
            value="Synthetic detector",
            field_role="event_field",
        )
        generated = self.parse(answer, first)
        reviewed = self.parse(answer, build("review", generated))
        self.assertTrue(reviewed["validation_issues"])
        self.assertFalse(reviewed["topic_submission_ready"])

    def test_uncertain_and_detection_role_still_withheld(self):
        for role in ("uncertain", "detection_metadata"):
            self.reply["filter"]["match"]["field_role"] = role
            generated = self.parse()
            reviewed = self.parse(req=request("review", generated))
            self.assertFalse(reviewed["topic_submission_ready"])

    def test_fence_warning_not_lost_when_converting_to_flat(self):
        generated = self.parse()
        second = request("review", generated)
        parsed = ai.parse_reply("```json\n" + json.dumps(self.reply), request=second)
        self.assertFalse(parsed["topic_submission_ready"])
        self.assertIn("missing_closing_json_fence", parsed["output_warnings"])

    def test_identical_duplicates_are_traced_conflicts_block(self):
        text = json.dumps(self.reply)[:-1] + ',"status":"proposal"}'
        parsed = ai.parse_reply(text, request=self.request)
        self.assertEqual(
            parsed["json_diagnostics"]["identical_duplicates_collapsed"], 1
        )
        with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
            ai.parse_reply(text[:-1] + ',"status":"no_change"}', request=self.request)

    def test_no_change_requires_null_filter_and_withhold(self):
        self.reply.update(status="no_change", filter=None)
        self.reply["topic_review"]["decision"] = "withhold"
        generated = self.parse()
        self.assertIsNone(generated["filter"])
        self.assertFalse(
            self.parse(req=request("review", generated))["topic_submission_ready"]
        )
        self.reply["topic_review"]["decision"] = "submit"
        with self.assertRaises(ValueError):
            self.parse()

    def test_visible_reference_checks_and_required_fields_remain(self):
        for mutate in (
            lambda x: x.update(sample_alert_ids=["not-visible"]),
            lambda x: x.pop("filter"),
            lambda x: x.pop("certainty"),
        ):
            answer = copy.deepcopy(self.reply)
            mutate(answer)
            with self.assertRaises(ValueError):
                self.parse(answer)


class TreeFlowTests(unittest.TestCase):
    def run_flow(self, mode="valid"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        requests = []

        def generate(req):
            requests.append(req)
            answer = tree_reply(req)
            if mode == "flat":
                answer = fixtures.reply(req)
            if req["phase"] == "review":
                if mode == "missing_role":
                    del answer["filter"]["match"]["field_role"]
                if mode == "withhold":
                    answer["topic_review"]["decision"] = "withhold"
                if mode == "no_change":
                    answer.update(status="no_change", filter=None)
                    answer["topic_review"]["decision"] = "withhold"
            return {**fixtures.response(req), "content": json.dumps(answer)}

        submit = Mock(return_value={"success": True, "code": 200, "rule_id": 123})
        processor = fixtures.module.TopicProcessor(
            Path(tmp.name),
            load_case=lambda _: fixtures.case(),
            load_rules=lambda: Mock(get_rule=Mock(return_value=fixtures.rule())),
            generate=generate,
            model="synthetic-model",
            budget=fixtures.Budget(),
            submit=submit,
            ai_contract=ai.CONTRACT,
        )
        result = processor.process(fixtures.signal())
        return processor, result, requests, submit

    def test_compiled_flat_payload_two_calls_one_post_and_compact_audit(self):
        processor, result, calls, submit = self.run_flow()
        self.assertEqual(result["status"], "received")
        self.assertEqual(len(calls), 2)
        self.assertTrue(
            all(c["topic_output_contract_version"] == ai.CONTRACT for c in calls)
        )
        submit.assert_called_once()
        payload = submit.call_args.args[0]
        self.assertEqual(set(payload), {"topic", "A", "expression", "executeType"})
        self.assertEqual(payload["executeType"], [3])
        self.assertEqual(set(payload["A"]), {"name", "operator", "value"})
        self.assertTrue(processor.verify_durable_result(fixtures.signal(), result))
        folder = Path(result["output_dir"])
        for phase in ("generate", "review"):
            summary = json.loads(
                (folder / (phase + ".summary.json")).read_text(encoding="utf-8")
            )
            self.assertEqual(
                summary["tree_compilation"]["contract_version"], ai.CONTRACT
            )
        text = "".join(p.read_text(encoding="utf-8") for p in folder.glob("*.json"))
        self.assertNotIn("NEVER-PERSIST-RAW", text)
        self.assertLess(len(text.encode()), 64 * 1024)
        self.assertEqual(
            processor.process(fixtures.signal())["record_id"], result["record_id"]
        )
        self.assertEqual(len(calls), 2)
        submit.assert_called_once()

    def test_legacy_flat_answer_never_submitted_under_new_contract(self):
        _, result, calls, submit = self.run_flow("flat")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(calls), 1)
        submit.assert_not_called()

    def test_invalid_review_no_post_no_third_call(self):
        _, result, calls, submit = self.run_flow("missing_role")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(calls), 2)
        submit.assert_not_called()
        self.assertTrue(result["reply_diagnostics"]["error_path"])

    def test_withhold_and_no_change_do_not_create(self):
        for mode, status in (
            ("withhold", "manual_review"),
            ("no_change", "not_applicable"),
        ):
            _, result, calls, submit = self.run_flow(mode)
            self.assertEqual(result["status"], status)
            self.assertEqual(len(calls), 2)
            submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
