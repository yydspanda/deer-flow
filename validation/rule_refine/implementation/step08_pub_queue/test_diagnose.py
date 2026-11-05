"""Read-only diagnosis, including private-value redaction and standalone execution."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import build_command
import build_diagnostic
import diagnose_sources as diagnostic


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="queue-diagnostic-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        for name, data in build_command.baseline_sources().items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.reference = build_diagnostic.make_reference()

    def snapshot(self):
        return {
            str(p.relative_to(self.root)): diagnostic.digest(p.read_bytes())
            for p in self.root.rglob("*")
            if p.is_file() and not p.is_symlink()
        }

    def test_all_sources_read_without_project_mutation(self):
        before = self.snapshot()
        report = diagnostic.diagnose(self.root, self.reference)
        self.assertEqual(report["files_checked"], 22)
        self.assertEqual(report["unrecognized_files"], [])
        self.assertEqual(report["files_written"], 0)
        self.assertFalse(report["installation_allowed_by_this_diagnosis"])
        self.assertEqual(self.snapshot(), before)

    def test_all_mismatches_returned_together(self):
        targets = ["llm_service/litellm_proxy_config.prd.yaml", "tasks/task.py"]
        for name in targets:
            path = self.root / name
            path.write_bytes(path.read_bytes() + b"\n# private-sentinel-comment\n")
        report = diagnostic.diagnose(self.root, self.reference)
        self.assertEqual(set(report["unrecognized_files"]), set(targets))
        self.assertEqual(len(report["files"]), 22)
        self.assertTrue(
            "private-sentinel-comment" not in json.dumps(report), "comment leaked"
        )

    def test_yaml_strings_keys_addresses_and_comments_hidden(self):
        sentinel = "sensitive-sentinel-abcd"
        lines = [
            f"    api_key: {sentinel}",
            f"  master_key: '{sentinel}'",
            f"    api_base: https://{sentinel}/v1",
            f"# {sentinel}",
            f"    headers: {{Authorization: {sentinel}}}",
            f"    unknown_{sentinel}: 12345",
            f"  - model_name: {sentinel}",
            f"    timeout: {sentinel}",
            f"    model: openai/{sentinel}",
            f"    password: {sentinel}",
        ]
        reference = diagnostic.yaml_reference(b"model_list:\n")
        output = json.dumps(
            diagnostic.yaml_difference("\n".join(lines).encode(), reference)
        )
        self.assertTrue(sentinel not in output, "private scalar or comment leaked")
        self.assertTrue("https://" not in output, "endpoint leaked")
        self.assertTrue("12345" not in output, "unknown-key value leaked")

    def test_only_allowlisted_alias_and_typed_values_visible(self):
        self.assertEqual(
            diagnostic.line_summary("  - model_name: 'Pub_DeepSeek_V4_Flash'")[
                "model_alias"
            ],
            "Pub_DeepSeek_V4_Flash",
        )
        self.assertEqual(
            diagnostic.line_summary("  max_chat_completion_tokens: 8192")["value"],
            "integer_hidden",
        )
        self.assertIs(
            diagnostic.line_summary("  drop_params: true")["setting_value"], True
        )
        self.assertEqual(
            diagnostic.line_summary("  timeout: 1234567890")["value"], "hidden"
        )
        self.assertEqual(diagnostic.line_summary("  api_key: 8192")["value"], "hidden")
        self.assertEqual(
            diagnostic.line_summary("  master_key: true")["value"], "hidden"
        )

    def test_settings_looking_numeric_secret_inside_headers_or_block_hidden(self):
        for source in (
            b"headers:\n  max_tokens: 123456789\n",
            b"api_key: |\n  max_tokens: 123456789\n",
        ):
            output = json.dumps(
                diagnostic.yaml_difference(
                    source, diagnostic.yaml_reference(b"model_list:\n")
                )
            )
            self.assertTrue(
                "123456789" not in output, "nested numeric credential leaked"
            )

    def test_format_diagnostics_do_not_claim_semantic_equivalence(self):
        baseline = b"model_list:\n  - model_name: DeepSeek_V32\n"
        changed = b"\xef\xbb\xbf" + baseline.replace(b"\n", b"\r\n")
        report = diagnostic.yaml_difference(
            changed, diagnostic.yaml_reference(baseline)
        )
        self.assertTrue(report["has_utf8_bom"])
        self.assertEqual(report["crlf_count"], 2)
        self.assertEqual(
            report["normalized_lf_no_bom_sha256"], diagnostic.digest(baseline)
        )
        self.assertEqual(
            report["analysis_scope"],
            "line_shapes_and_allowlisted_literals_not_yaml_semantics",
        )

    def test_links_and_missing_files_not_followed(self):
        name = "llm_service/litellm_proxy_config.prd.yaml"
        path = self.root / name
        path.unlink()
        path.symlink_to(Path(self.temporary.name) / "not-read")
        (self.root / "run.sh").unlink()
        report = diagnostic.diagnose(self.root, self.reference)
        rows = {row["file"]: row for row in report["files"]}
        self.assertEqual(rows[name]["status"], "symlink_not_read")
        self.assertEqual(rows["run.sh"]["status"], "file_not_readable")

    def test_long_diff_output_bounded(self):
        report = diagnostic.yaml_difference(
            b"# hidden comment\n" * 1000, diagnostic.yaml_reference(b"model_list:\n")
        )
        count = sum(
            len(item["current_lines"]) + len(item["reviewed_lines"])
            for item in report["differences"]
        )
        self.assertLessEqual(count, 32)
        self.assertTrue(
            any(item["details_truncated"] for item in report["differences"])
        )

    def test_standalone_command_no_files_written_or_repository_needed(self):
        command = Path(self.temporary.name) / "DIAGNOSE.sh"
        command.write_bytes(
            (build_diagnostic.HERE / "DIAGNOSE-SOURCE-DIFFERENCES.sh").read_bytes()
        )
        before = self.snapshot()
        process = subprocess.run(
            ["bash", str(command)],
            cwd=self.temporary.name,
            env=dict(os.environ, SEC_KNOW_PROJECT_ROOT=str(self.root)),
            capture_output=True,
            text=True,
        )
        self.assertTrue(process.returncode == 0, "standalone diagnosis failed")
        report = json.loads(process.stdout.split("\n", 1)[1])
        self.assertEqual(report["status"], "all_files_recognized")
        self.assertEqual(report["files_checked"], 22)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(Path(self.temporary.name).iterdir()), {self.root, command})


if __name__ == "__main__":
    unittest.main()
