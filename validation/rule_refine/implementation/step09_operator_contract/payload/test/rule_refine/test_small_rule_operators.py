"""Offline small-rule wire contracts; no app configuration or real transport imports."""

import asyncio
import copy
import hashlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
NEW_OPERATORS = ("Eq", "Ne", "Contains", "NotContains", "StartsWith", "EndsWith")
OLD_OPERATORS = ("equal", "notequal", "contain", "notcontain", "startWith", "endWith")


def module_from_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rule(operator="Eq"):
    return {
        "topic": "SYNTHETIC_TEST_TOPIC",
        "A": {
            "name": "command",
            "value": '合成任务 \\path "quoted"',
            "operator": operator,
        },
        "expression": "A",
        "executeType": [1],
    }


def load_client():
    operators = module_from_file(
        "_smallrule_test_operators",
        ROOT / "my_workflows/zeus/rule_refine/operators.py",
    )
    modules = {}
    for name in (
        "util",
        "my_workflows",
        "my_workflows.zeus",
        "my_workflows.zeus.rule_refine",
    ):
        module = types.ModuleType(name)
        module.__path__ = []
        modules[name] = module
    modules["my_workflows.zeus.rule_refine.operators"] = operators
    logger_module = types.ModuleType("util.log_config")
    logger_module.get_logger = lambda: Mock()
    modules[logger_module.__name__] = logger_module
    config = types.ModuleType("util.root_config")
    config.ZEUS_APP_ID = "SYNTHETIC_APP"
    config.ZEUS_APP_KEY = "synthetic-not-a-secret"
    config.ZEUS_SYSTEM_URL = "https://gateway.invalid"
    modules[config.__name__] = config
    signing = types.ModuleType("util.util_tools")
    signed_payloads = []

    def sign(*, data, app_id, app_key):
        # Preserve the production signer's JSON-serialization contract without credentials.
        body = json.dumps(data).encode("utf-8")
        signed_payloads.append(body)
        return {"App-Id": app_id, "App-Sign": hashlib.sha256(body).hexdigest()}

    signing.isec_sign = sign
    modules[signing.__name__] = signing
    requests = types.ModuleType("requests")
    requests.post = Mock(side_effect=AssertionError("real HTTP is forbidden"))
    modules[requests.__name__] = requests
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientTimeout = lambda **kwargs: kwargs
    aiohttp.ClientSession = Mock(side_effect=AssertionError("real HTTP is forbidden"))
    modules[aiohttp.__name__] = aiohttp
    with patch.dict(sys.modules, modules):
        client = module_from_file(
            "_smallrule_test_client",
            ROOT
            / "my_workflows/zeus/flows/disposition_tools/small_rule_sync_client.py",
        )
    return client, signed_payloads


class SmallRuleOperatorTests(unittest.TestCase):
    def setUp(self):
        self.module, self.signed = load_client()
        self.client = self.module.SmallRuleSyncClient()

    def test_all_six_new_values_are_valid_and_unchanged(self):
        self.assertEqual(set(self.module.VALID_OPERATORS), set(NEW_OPERATORS))
        for operator in NEW_OPERATORS:
            with self.subTest(operator=operator):
                sample = rule(operator)
                original = copy.deepcopy(sample)
                self.module.validate_small_rule(sample)
                body = self.client._build_request([sample])
                self.assertEqual(json.loads(body["payload_str"]), [original])
                self.assertEqual(sample, original)
                self.assertEqual(body["payload_str"].encode("utf-8"), self.signed[-1])

    def test_old_values_and_wrong_case_fail_before_signing(self):
        for operator in (*OLD_OPERATORS, "EQ", "contains", "Unknown", " Eq", "Eq "):
            for validate in (True, False):
                with self.subTest(operator=operator, validate=validate):
                    with self.assertRaises(ValueError):
                        self.client._build_request([rule(operator)], validate=validate)
        self.assertEqual(self.signed, [])

    def test_non_string_operators_fail_without_type_error_or_signing(self):
        for operator in (None, False, 1, [], {}, ["Eq"]):
            for validate in (True, False):
                with self.subTest(operator=operator, validate=validate):
                    with self.assertRaises(ValueError):
                        self.client._build_request([rule(operator)], validate=validate)
        self.assertEqual(self.signed, [])

    def test_invalid_later_batch_entry_prevents_signing_entire_batch(self):
        with self.assertRaises(ValueError):
            self.client._build_request([rule("Eq"), rule("equal")])
        self.assertEqual(self.signed, [])

    def test_sync_sends_exact_signed_bytes_for_all_six_values(self):
        response = Mock()
        response.json.return_value = {
            "code": 200,
            "message": "成功",
            "data": "synthetic-id",
        }
        self.module.requests.post = Mock(return_value=response)
        rules = [rule(operator) for operator in NEW_OPERATORS]
        original = copy.deepcopy(rules)
        result = self.client.sync_small_rule(rules)
        self.assertTrue(result["success"])
        self.module.requests.post.assert_called_once()
        args, kwargs = self.module.requests.post.call_args
        self.assertEqual(args, ("https://gateway.invalid/public/syncSmallRule",))
        self.assertEqual(kwargs["data"], self.signed[-1])
        self.assertEqual(
            kwargs["headers"]["App-Sign"], hashlib.sha256(kwargs["data"]).hexdigest()
        )
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(json.loads(kwargs["data"]), original)
        self.assertEqual(rules, original)
        response.raise_for_status.assert_called_once()

    def test_async_sends_exact_signed_bytes_for_all_six_values(self):
        calls = []

        class Response:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def raise_for_status(self):
                return None

            async def json(self):
                return {"code": 200, "message": "成功", "data": "synthetic-id"}

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def post(self, url, **kwargs):
                calls.append((url, kwargs))
                return Response()

        self.module.aiohttp.ClientSession = Mock(return_value=Session())
        rules = [rule(operator) for operator in NEW_OPERATORS]
        original = copy.deepcopy(rules)
        result = asyncio.run(self.client.async_small_rule(rules))
        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertEqual(url, "https://gateway.invalid/public/syncSmallRule")
        self.assertEqual(kwargs["data"], self.signed[-1])
        self.assertEqual(
            kwargs["headers"]["App-Sign"], hashlib.sha256(kwargs["data"]).hexdigest()
        )
        self.assertEqual(json.loads(kwargs["data"]), original)
        self.assertEqual(rules, original)

    def test_validation_false_cannot_submit_retired_values_in_either_transport(self):
        with self.assertRaises(ValueError):
            self.client.sync_small_rule([rule("equal")], validate=False)
        with self.assertRaises(ValueError):
            asyncio.run(self.client.async_small_rule([rule("contain")], validate=False))
        self.module.requests.post.assert_not_called()
        self.module.aiohttp.ClientSession.assert_not_called()
        self.assertEqual(self.signed, [])

    def test_flat_terms_contract_and_expression_checks_are_preserved(self):
        sample = rule()
        nested = {key: value for key, value in sample.items() if key != "A"}
        nested["terms"] = {"A": sample["A"]}
        with self.assertRaises(ValueError):
            self.client._build_request([nested])
        sample["expression"] = "B"
        with self.assertRaises(ValueError):
            self.client._build_request([sample])
        self.assertEqual(self.signed, [])

    def test_operator_error_explanation_lists_new_contract(self):
        message = self.module.SMALL_RULE_ERROR_MESSAGES[69208]
        for operator in NEW_OPERATORS:
            self.assertIn(operator, message)
        self.assertNotIn("equal/contain/notcontain", message)


if __name__ == "__main__":
    unittest.main()
