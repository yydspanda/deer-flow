"""Bounded registry edits against opaque, otherwise preserved private source."""

import hashlib
import json
from pathlib import Path
import unittest
import zipfile

import scoped_proxy


ROOT = Path(__file__).resolve().parents[3]
YAML = "llm_service/litellm_proxy_config.prd.yaml"
PYTHON = "llm_service/openai_completion.py"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def original(path):
    with zipfile.ZipFile(ROOT / "sec_know_model.zip") as archive:
        return archive.read(path)


class ScopedProxyTests(unittest.TestCase):
    def test_yaml_extra_model_preserved_and_targets_removed(self):
        baseline = original(YAML)
        insertion = b"  - model_name: Additional_Internal_Model\n    litellm_params:\n      model: openai/Additional_Internal_Model\n      api_base: http://private-sentinel\n      api_key: secret-sentinel\n\n"
        variant = baseline.replace(
            b"litellm_settings:", insertion + b"litellm_settings:"
        )
        contract = scoped_proxy.build_contract(YAML, baseline)
        after, proof = scoped_proxy.apply(YAML, variant, contract)
        self.assertEqual(proof["state"], "before")
        self.assertTrue(proof["non_target_bytes_preserved"])
        self.assertTrue(insertion in after, "additional registration was changed")
        for model in scoped_proxy.RETIRED:
            self.assertTrue(
                ("model_name: " + model).encode() not in after, "retired alias remains"
            )
        self.assertTrue(
            after.split(b"litellm_settings:", 1)[1]
            == baseline.split(b"litellm_settings:", 1)[1],
            "settings changed",
        )
        second, second_proof = scoped_proxy.apply(YAML, after, contract)
        self.assertEqual(digest(after), digest(second))
        self.assertEqual(second_proof["state"], "after")

    def test_python_added_registry_and_unrelated_code_untouched(self):
        baseline = original(PYTHON)
        marker = b'    "Pub_DeepSeek_V4_Flash": {'
        added = b'    "Additional_Internal_Model": {"model": "opaque", "key": "private-sentinel"},\n'
        variant = (
            baseline.replace(marker, added + marker, 1)
            + b"\n# Local unrelated change must survive.\n"
        )
        contract = scoped_proxy.build_contract(PYTHON, baseline)
        after, proof = scoped_proxy.apply(PYTHON, variant, contract)
        self.assertTrue(added in after, "additional registration changed")
        self.assertTrue(
            after.endswith(b"# Local unrelated change must survive.\n"),
            "unrelated code changed",
        )
        self.assertTrue(proof["non_target_bytes_preserved"])
        second, second_proof = scoped_proxy.apply(PYTHON, after, contract)
        self.assertEqual(digest(second), digest(after))
        self.assertEqual(second_proof["state"], "after")

    def test_contract_contains_only_hashes_and_public_metadata(self):
        for path in (YAML, PYTHON):
            contract = scoped_proxy.build_contract(path, original(path))
            encoded = json.dumps(contract)
            self.assertTrue("http://" not in encoded, "endpoint entered contract")
            self.assertTrue(
                "api_key" not in encoded, "private entry source entered contract"
            )
            self.assertEqual(
                set(contract["retired_region_sha256"]), set(scoped_proxy.RETIRED)
            )

    def test_modified_retired_regions_refused(self):
        for path in (YAML, PYTHON):
            baseline = original(path)
            contract = scoped_proxy.build_contract(path, baseline)
            # Whitespace is still part of the exact reviewed target fragment.
            marker = (
                b"  - model_name: DeepSeek_V32\n"
                if path == YAML
                else b'    "DeepSeek_V32": {\n'
            )
            changed = (
                marker + b"    # changed target block\n"
                if path == YAML
                else marker.rstrip(b"\n") + b" \n"
            )
            variant = baseline.replace(marker, changed, 1)
            with self.assertRaisesRegex(ValueError, "retired_region_changed"):
                scoped_proxy.apply(path, variant, contract)

    def test_partial_retirement_refused(self):
        for path in (YAML, PYTHON):
            baseline = original(path)
            contract = scoped_proxy.build_contract(path, baseline)
            variant = baseline.replace(b"DeepSeek_V32", b"Unrelated_V32")
            with self.assertRaisesRegex(ValueError, "partial_retirement"):
                scoped_proxy.apply(path, variant, contract)

    def test_yaml_duplicate_model_and_duplicate_registry_refused(self):
        baseline = original(YAML)
        contract = scoped_proxy.build_contract(YAML, baseline)
        for added in (
            b"  - model_name: Pub_DeepSeek_V4_Flash\n    litellm_params:\n      model: duplicate\n",
            b"model_list:\n",
        ):
            variant = baseline.replace(
                b"litellm_settings:", added + b"litellm_settings:"
            )
            with self.assertRaises(ValueError):
                scoped_proxy.apply(YAML, variant, contract)

    def test_yaml_unsupported_multiline_anchor_tag_and_duplicate_key_refused(self):
        baseline = original(YAML)
        contract = scoped_proxy.build_contract(YAML, baseline)
        for leaf in (
            b"      api_key: |\n        hidden\n",
            b"      api_key: &anchor value\n",
            b"      api_key: !tag value\n",
            b"      model: duplicate\n",
        ):
            added = (
                b"  - model_name: Extra\n    litellm_params:\n      model: opaque\n"
                + leaf
            )
            variant = baseline.replace(
                b"litellm_settings:", added + b"litellm_settings:"
            )
            with self.assertRaises(ValueError):
                scoped_proxy.apply(YAML, variant, contract)

    def test_yaml_settings_envelope_change_refused(self):
        baseline = original(YAML)
        contract = scoped_proxy.build_contract(YAML, baseline)
        variant = baseline.replace(b"  drop_params: true", b"  drop_params: false")
        with self.assertRaisesRegex(ValueError, "yaml_envelope_changed"):
            scoped_proxy.apply(YAML, variant, contract)

    def test_yaml_alias_override_and_empty_params_refused(self):
        baseline = original(YAML)
        contract = scoped_proxy.build_contract(YAML, baseline)
        for added in (
            b"  - model_name: Extra\n    model_name: DeepSeek_V32\n    litellm_params:\n      model: opaque\n",
            b"  - model_name: Extra\n    litellm_params:\n      api_key: opaque\n",
        ):
            variant = baseline.replace(
                b"litellm_settings:", added + b"litellm_settings:"
            )
            with self.assertRaises(ValueError):
                scoped_proxy.apply(YAML, variant, contract)

    def test_python_duplicates_nonliteral_and_same_line_targets_refused(self):
        baseline = original(PYTHON)
        contract = scoped_proxy.build_contract(PYTHON, baseline)
        duplicate = baseline.replace(
            b'    "Pub_DeepSeek_V4_Flash": {', b'    "DeepSeek_V32": {', 1
        )
        duplicate_assignment = baseline + b"\nPRD_MODEL_CONFIGS = {}\n"
        for variant in (duplicate, duplicate_assignment):
            with self.assertRaises(ValueError):
                scoped_proxy.apply(PYTHON, variant, contract)
        toy = b'PRD_MODEL_CONFIGS = {"DeepSeek_V32": {}, "DeepSeek_V4_Flash_My": {}, "Pub_DeepSeek_V4_Flash": {}}\n'
        with self.assertRaises(ValueError):
            scoped_proxy.build_contract(PYTHON, toy)

    def test_no_model_or_code_execution(self):
        baseline = original(PYTHON)
        contract = scoped_proxy.build_contract(PYTHON, baseline)
        added = b'    "Additional_Internal_Model": dangerous_call(),\n'
        variant = baseline.replace(
            b'    "Pub_DeepSeek_V4_Flash": {',
            added + b'    "Pub_DeepSeek_V4_Flash": {',
            1,
        )
        # Unknown entry values are syntax only. Executing this file would fail.
        after, proof = scoped_proxy.apply(PYTHON, variant, contract)
        self.assertTrue(added in after, "non-target expression changed")
        self.assertEqual(proof["state"], "before")

    def test_baseline_result_matches_existing_reviewed_transform(self):
        import proxy_transforms

        for path in (YAML, PYTHON):
            baseline = original(path)
            expected = proxy_transforms.transforms(
                path, baseline.decode("utf-8")
            ).encode("utf-8")
            actual, _ = scoped_proxy.apply(
                path, baseline, scoped_proxy.build_contract(path, baseline)
            )
            self.assertEqual(digest(actual), digest(expected))


if __name__ == "__main__":
    unittest.main()
