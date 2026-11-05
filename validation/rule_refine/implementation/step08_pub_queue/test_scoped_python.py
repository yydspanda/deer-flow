"""Scoped target editing without executing private source or unknown extensions."""

import ast
import hashlib
from pathlib import Path
import unittest
import zipfile

import scoped_python as scoped

ZIP = Path(__file__).resolve().parents[3] / "sec_know_model.zip"


def baseline(path):
    with zipfile.ZipFile(ZIP) as archive:
        return archive.read(path)


def digest(value):
    return hashlib.sha256(value).hexdigest()


class ScopedPythonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {path: baseline(path) for path in scoped.SUPPORTED_PATHS}
        cls.contracts = {
            path: scoped.build_contract(path, data)
            for path, data in cls.sources.items()
        }

    def apply(self, path, source):
        if isinstance(source, str):
            source = source.encode("utf-8")
        return scoped.apply(path, source, self.contracts[path])

    def test_controller_baseline_and_idempotence(self):
        after, proof = self.apply(scoped.CONTROLLER, self.sources[scoped.CONTROLLER])
        self.assertEqual(proof["state"], "before")
        self.assertTrue(proof["outside_target_bytes_preserved"])
        self.assertFalse(proof["target_source_executed"])
        again, second = self.apply(scoped.CONTROLLER, after)
        self.assertEqual(digest(again), digest(after))
        self.assertEqual(second["state"], "after")
        self.assertEqual(second["edit_ranges"], [])
        tree = ast.parse(after)
        self.assertEqual(
            sum(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "resolve_alert_model"
                for n in ast.walk(tree)
            ),
            1,
        )

    def test_controller_unrelated_bytes_defaults_and_methods_preserved(self):
        original = self.sources[scoped.CONTROLLER].decode("utf-8")
        extension = '\n# local unrelated sentinel 中文\ndef independently_added():\n    return "local-value"\n'
        current = original.replace('mode="online"', 'mode="valid"', 1) + extension
        changed, _ = self.apply(scoped.CONTROLLER, current)
        self.assertTrue(
            changed.decode("utf-8").endswith(extension), "unrelated source changed"
        )
        self.assertTrue(
            'mode="valid"' in changed.decode("utf-8"), "unrelated default changed"
        )
        self.assertEqual(changed.count(b"independently_added"), 1)

    def test_controller_current_qwen_branch_is_reviewed_and_preserved_in_resolution(
        self,
    ):
        original = self.sources[scoped.CONTROLLER]
        current = original.replace(
            b'model_name = "PingAnGPT_R"', b'model_name = "Qwen38_27B"', 1
        )
        self.assertEqual(
            digest(current),
            "d24a93c167428804d1ac606f3a034b168592e3fd1e692d6cf1e26b1104329022",
        )
        changed, proof = self.apply(scoped.CONTROLLER, current)
        self.assertEqual(proof["state"], "before")
        self.assertTrue(proof["outside_target_bytes_preserved"])
        self.assertEqual(
            changed.count(b"model_name = resolve_alert_model(model_name, rc.ENV)"), 1
        )
        self.assertNotIn(b'model_name = "PingAnGPT_R"', changed)
        again, second = self.apply(scoped.CONTROLLER, changed)
        self.assertEqual(digest(again), digest(changed))
        self.assertEqual(second["state"], "after")

    def test_controller_changed_branch_rejected(self):
        original = self.sources[scoped.CONTROLLER].decode("utf-8")
        changed = original.replace(
            'model_name = "PingAnGPT_R"', 'model_name = "different-local-model"', 1
        )
        with self.assertRaisesRegex(ValueError, "resolution_block"):
            self.apply(scoped.CONTROLLER, changed)

    def test_controller_additional_reassignment_or_pre_resolution_use_rejected(self):
        original = self.sources[scoped.CONTROLLER].decode("utf-8")
        marker = "        self.model_name = model_name"
        for inserted in (
            '        model_name = "extra"\n',
            '        self.model_name = "extra"\n',
            "        del model_name\n",
        ):
            with self.subTest(kind=digest(inserted.encode())):
                with self.assertRaises(ValueError):
                    self.apply(
                        scoped.CONTROLLER,
                        original.replace(marker, inserted + marker, 1),
                    )
        early = original.replace(
            "        self.session_id = session_id",
            "        print(model_name)\n        self.session_id = session_id",
            1,
        )
        with self.assertRaisesRegex(ValueError, "before_resolution"):
            self.apply(scoped.CONTROLLER, early)

    def test_controller_partial_state_and_duplicate_class_rejected(self):
        original = self.sources[scoped.CONTROLLER].decode("utf-8")
        for current in (
            original.replace('model_name="DeepSeek_V32"', "model_name=None", 1),
            original + "\nclass DataController:\n    pass\n",
        ):
            with self.assertRaises(ValueError):
                self.apply(scoped.CONTROLLER, current)
        after, _ = self.apply(scoped.CONTROLLER, original)
        with self.assertRaises(ValueError):
            self.apply(
                scoped.CONTROLLER,
                after.replace(
                    b"from config.model_limits import resolve_alert_model\n", b""
                ),
            )

    def test_controller_resolver_name_shadowing_rejected(self):
        original = self.sources[scoped.CONTROLLER].decode("utf-8")
        for prefix in (
            "resolve_alert_model = unsafe_value\n",
            "def resolve_alert_model(*args):\n    pass\n",
            "from elsewhere import other as resolve_alert_model\n",
        ):
            with self.assertRaisesRegex(ValueError, "resolver_name_conflicts"):
                self.apply(scoped.CONTROLLER, prefix + original)
        with self.assertRaisesRegex(ValueError, "rc_name_conflicts"):
            self.apply(scoped.CONTROLLER, original + "\nrc = changed_configuration\n")

    def test_models_extra_alias_and_removed_override_supported(self):
        original = self.sources[scoped.MODELS].decode("utf-8")
        current = original.replace(
            'models = ["DeepSeek_V4_Flash"]', "# local override removed", 1
        ).replace("models = [\n", 'models = [\n    "new_private_model",\n', 1)
        changed, proof = self.apply(scoped.MODELS, current)
        tree = ast.parse(changed)
        values = [
            value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "models" for t in node.targets)
            for value in node.value.elts
        ]
        self.assertIn("new_private_model", values)
        self.assertFalse(scoped.RETIRED.intersection(values))
        self.assertEqual(proof["state"], "before")
        expected = current.replace('"DeepSeek_V4_Flash_My",', "", 1).replace(
            '"DeepSeek_V32",', "", 1
        )
        self.assertTrue(
            changed.decode("utf-8") == expected,
            "bytes outside retired literals and separators changed",
        )
        again, second = self.apply(scoped.MODELS, changed)
        self.assertEqual(digest(again), digest(changed))
        self.assertEqual(second["state"], "after")

    def test_models_inline_positions_comments_and_implicit_strings(self):
        cases = (
            'models = ["DeepSeek_V32", "keep", "DeepSeek_V4_Flash_My"]\n',
            'models = ["keep", "DeepSeek_V32", "DeepSeek_V4_Flash_My"]\n',
            'models = ["DeepSeek_V32", "DeepSeek_V4_Flash_My", "keep"]\n',
            'models = ["DeepSeek_" "V32", # comment retained\n "keep", "DeepSeek_V4_Flash_My",]\n',
        )
        for current in cases:
            with self.subTest(sha=digest(current.encode())):
                after, _ = self.apply(scoped.MODELS, current)
                self.assertEqual(
                    ast.literal_eval(ast.parse(after).body[0].value), ["keep"]
                )
                if "comment retained" in current:
                    self.assertTrue(
                        b"comment retained" in after, "comment unexpectedly removed"
                    )

    def test_models_changed_literals_dynamic_writes_empty_and_duplicates_rejected(self):
        cases = (
            'models = ["keep", unknown_value]\n',
            "models = some_function()\n",
            'models = other = ["DeepSeek_V32", "keep"]\n',
            'models = ["DeepSeek_V32"]\n',
            'models = ["keep", "DeepSeek_V32", "DeepSeek_V32"]\n',
            'models = ["keep"]\nmodels += ["DeepSeek_V32"]\n',
            'models = ["keep"]\nmodels.append("DeepSeek_V32")\n',
            'models = ["keep"]\nmodels[0] = "DeepSeek_V32"\n',
            'models = ["keep"]\nif True:\n    models = ["DeepSeek_V32"]\n',
            'models = ["keep"]\ndef unsafe():\n    global models\n    models = ["DeepSeek_V32"]\n',
        )
        for current in cases:
            with self.subTest(sha=digest(current.encode())):
                with self.assertRaises(ValueError):
                    self.apply(scoped.MODELS, current)

    def test_unknown_code_is_only_parsed_and_never_executed(self):
        source = (
            'raise RuntimeError("not executed")\nmodels = ["DeepSeek_V32", "keep"]\n'
        )
        changed, proof = self.apply(scoped.MODELS, source)
        self.assertTrue(
            changed.startswith(b'raise RuntimeError("not executed")'),
            "unknown code modified",
        )
        self.assertFalse(proof["target_source_executed"])

    def test_contracts_only_contain_hashes_and_fixed_policy(self):
        for path, contract in self.contracts.items():
            self.assertEqual(contract["path"], path)
            self.assertEqual(contract["schema"], "sec_know.scoped_python.v1")
            with self.assertRaisesRegex(ValueError, "contract_mismatch"):
                scoped.apply(path, self.sources[path], {})


if __name__ == "__main__":
    unittest.main()
