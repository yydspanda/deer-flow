"""Advisory compatibility with synthetic answers, not live model quality tests."""

import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    "_topic_advisory_fixtures", Path(__file__).with_name("test_topic_processor.py")
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
ai = importlib.import_module(fixtures.PACKAGE + ".topic_tree_ai")


def leaf(name, value, operator="Eq"):
    return {
        "name": name,
        "operator": operator,
        "value": value,
        "field_role": "event_field",
        "basis": "人工样本原始事件中的字段，仅作离线格式验证。",
    }


def material(topic, fields):
    case = fixtures.case()
    case["rule"]["topic"] = topic
    case["original_rule"]["topic"] = topic
    case["history_samples"] = []
    case["coverage"]["history_selected_unique_alerts"] = 0
    for sample in case["current_samples"]:
        sample["raw_logs"] = [copy.deepcopy(fields)]
    return case


def request(case, phase="generate", draft=None):
    return ai.build_request(
        case["rule"],
        case["current_samples"],
        case["history_samples"],
        case["coverage"],
        phase=phase,
        draft=draft,
    )


def answer(req, match):
    result = fixtures.reply(req)
    result["filter"] = {
        "topic": req["context"]["rule"]["topic"],
        "match": copy.deepcopy(match),
    }
    result["issue"] = "本轮可见样本出现相同的任务操作。"
    result["recommendation"] = "提交共性条件建议，待运营确认。"
    result["certainty"] = "仅依据展示样本；无历史对照。"
    result["review_notes"] = "条件取自本轮原始事件，业务归属待运营确认。"
    result["topic_review"] = {
        "decision": "submit",
        "other_detection_impact": "匹配事件将不进入后续检测；待运营确认。",
    }
    return result


class TopicAdvisoryTests(unittest.TestCase):
    def parse(self, payload, req):
        return ai.parse_reply(json.dumps(payload, ensure_ascii=False), request=req)

    def check_rounds(self, case, match):
        first = request(case)
        generated = self.parse(answer(first, match), first)
        second = request(case, "review", generated)
        reviewed = self.parse(answer(second, match), second)
        self.assertEqual(first["prompt_version"], "topic-before-detection-advisory-v2")
        self.assertEqual(
            first["topic_output_contract_version"], "topic-condition-tree-v1"
        )
        self.assertFalse(generated["topic_submission_ready"])
        self.assertTrue(reviewed["topic_submission_ready"])
        self.assertEqual(reviewed["validation_issues"], [])
        self.assertEqual(reviewed["filter"]["topic"], case["rule"]["topic"])
        self.assertEqual(reviewed["condition_tree"]["match"], match)
        return reviewed

    def test_single_complete_operation_without_history_is_valid_advisory_shape(self):
        command = "workerctl inspect --scope inventory"
        case = material("audit-command-source", {"operation_text": command})
        reviewed = self.check_rounds(case, leaf("operation_text", command))
        self.assertEqual(set(reviewed["filter"]), {"topic", "A", "expression"})
        self.assertEqual(reviewed["filter"]["expression"], "A")
        self.assertEqual(reviewed["filter"]["A"]["value"], command)
        self.assertEqual(reviewed["recommendation"], "提交共性条件建议，待运营确认。")

    def test_two_original_fields_preserve_topic_and_joint_logic(self):
        case = material(
            "maintenance_records",
            {"team": "ops-alpha", "script_path": "/opt/tool-maintenance/check.sh"},
        )
        match = {
            "all": [
                leaf("team", "ops-alpha"),
                leaf("script_path", "/opt/tool-maintenance/", "StartsWith"),
            ]
        }
        reviewed = self.check_rounds(case, match)
        self.assertEqual(reviewed["filter"]["expression"], "(A && B)")
        self.assertEqual(reviewed["filter"]["A"]["name"], "team")
        self.assertEqual(reviewed["filter"]["B"]["operator"], "StartsWith")

    def test_three_conditions_keep_alternative_features_inside_joint_scope(self):
        case = material(
            "endpoint-events",
            {
                "kind": "routine_invocation",
                "command": "inventory-runner --collect versions",
                "parent_image": "managed-job-launcher",
            },
        )
        match = {
            "all": [
                leaf("kind", "routine_invocation"),
                {
                    "any": [
                        leaf("command", "inventory-runner", "Contains"),
                        leaf("parent_image", "managed-job-launcher"),
                    ]
                },
            ]
        }
        reviewed = self.check_rounds(case, match)
        self.assertEqual(reviewed["filter"]["expression"], "(A && (B || C))")
        self.assertEqual(
            set(reviewed["filter"]), {"topic", "A", "B", "C", "expression"}
        )

    def test_old_prompt_version_cannot_be_silently_reinterpreted(self):
        case = material("audit-command-source", {"operation_text": "inventory refresh"})
        req = request(case)
        old = {**req, "prompt_version": "topic-before-detection-tree-v1"}
        with self.assertRaisesRegex(ValueError, "topic_tree_request_contract_required"):
            self.parse(answer(req, leaf("operation_text", "inventory refresh")), old)

    def test_review_keeps_original_predicates_but_not_generation_judgment(self):
        case = material("audit-command-source", {"operation_text": "inventory refresh"})
        first = request(case)
        payload = answer(first, leaf("operation_text", "inventory refresh"))
        payload["filter"]["match"]["basis"] = "GENERATION-PRIVATE-BASIS"
        payload["recommendation"] = "GENERATION-PRIVATE-PROSE"
        generated = self.parse(payload, first)
        review = request(case, "review", generated)
        projection = review["context"]["draft"]["filter"]
        self.assertEqual(
            projection["match"],
            {"name": "operation_text", "operator": "Eq", "value": "inventory refresh"},
        )
        text = json.dumps(review, ensure_ascii=False)
        self.assertNotIn("GENERATION-PRIVATE-BASIS", text)
        self.assertNotIn("GENERATION-PRIVATE-PROSE", text)

    def test_execute_type_still_comes_from_host_materials(self):
        case = material("maintenance_records", {"operation_text": "inventory refresh"})
        case["execute_type"]["values"] = [2]
        signal = {**fixtures.signal(), "topic": case["rule"]["topic"]}
        requests = []

        def generate(req):
            requests.append(req)
            reply = answer(req, leaf("operation_text", "inventory refresh"))
            return {**fixtures.response(req), "content": json.dumps(reply)}

        submit = Mock(return_value={"success": True, "code": 200, "rule_id": 123})
        with tempfile.TemporaryDirectory(prefix="topic-advisory-fixture-") as folder:
            processor = fixtures.module.TopicProcessor(
                Path(folder),
                load_case=lambda _: copy.deepcopy(case),
                load_rules=lambda: Mock(get_rule=Mock(return_value=case["rule"])),
                generate=generate,
                model="synthetic-model",
                budget=fixtures.Budget(),
                submit=submit,
                ai_contract="topic-condition-tree-v1",
            )
            result = processor.process(signal)
        self.assertEqual(result["status"], "received")
        self.assertEqual(len(requests), 2)
        submit.assert_called_once()
        payload = submit.call_args.args[0]
        self.assertEqual(payload["executeType"], [2])
        self.assertEqual(payload["topic"], "maintenance_records")
        for req in requests:
            self.assertEqual(req["context"]["coverage"]["target_executeType"], [2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
