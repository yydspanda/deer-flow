"""Copyable command integrity and entry tests; never contact a provider."""

import ast
import base64
import copy
import json
import subprocess
import sys
import unittest
import zlib
from unittest.mock import patch

import build_command


class CommandTests(unittest.TestCase):
    def test_packet_contains_only_reviewed_code_sources(self):
        packet = build_command.contents()
        self.assertEqual(set(packet), {"files"})
        self.assertEqual(set(packet["files"]), set(build_command.ORDER))
        for name, path in build_command.source_paths().items():
            self.assertEqual(packet["files"][name], path.read_text(encoding="utf-8"))
            self.assertEqual(path.suffix, ".py")
        self.assertNotIn("surge_processor", packet["files"])
        self.assertNotIn("surge_runtime", packet["files"])

    def test_packaged_sources_parse_on_python311_and_roundtrip_exactly(self):
        packet = build_command.contents()
        source = build_command.runtime_source(packet)
        for text in packet["files"].values():
            ast.parse(text, feature_version=(3, 11))
        node = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "payload"
        )
        decoded = json.loads(
            zlib.decompress(base64.b64decode(ast.literal_eval(node.value)))
        )
        self.assertEqual(decoded, packet)

    def test_bootstrap_loads_dependencies_then_calls_only_real_runner(self):
        packet = build_command.contents()
        packet["files"]["runner"] += """
def main(argv=None, *, tool_hashes=None):
    assert argv == ['--live']
    assert 'surge_materials' in sys.modules
    assert 'materials' in sys.modules
    assert 'restricted_dataframe_pickle' in sys.modules
    assert set(tool_hashes) == {'surge_materials', 'restricted_dataframe_pickle', 'materials', 'runner'}
    assert all(len(value) == 64 for value in tool_hashes.values())
    return 17
"""
        with patch.dict(sys.modules), self.assertRaises(SystemExit) as result:
            exec(build_command.runtime_source(packet), {})
        self.assertEqual(result.exception.code, 17)

    def test_unknown_modules_and_invalid_syntax_rejected_during_build(self):
        packet = copy.deepcopy(build_command.contents())
        packet["files"]["unexpected"] = "raise AssertionError('must not run')"
        with self.assertRaisesRegex(ValueError, "unexpected_tool_modules"):
            build_command.runtime_source(packet)
        del packet["files"]["unexpected"]
        packet["files"]["runner"] = "def broken("
        with self.assertRaises(SyntaxError):
            build_command.runtime_source(packet)

    def test_bad_packet_hash_stops_before_module_execution(self):
        packet = {
            "files": {
                name: "raise AssertionError('must not import')"
                for name in build_command.ORDER
            }
        }
        source = build_command.runtime_source(packet)
        source = source.replace(
            "decoder = zlib.decompressobj()",
            "payload = payload[:-4] + 'AAAA'\ndecoder = zlib.decompressobj()",
        )
        with patch.dict(sys.modules), self.assertRaises((SystemExit, zlib.error)):
            exec(source, {})

    def test_shell_requires_local_profile_private_files_and_valid_syntax(self):
        command = build_command.command(build_command.contents())
        self.assertIn('cd "$HOME/sec_know_model"', command)
        self.assertIn("env env_profile=LOCAL .venv/bin/python -B -", command)
        self.assertIn("umask 077", command)
        self.assertNotIn("git ", command)
        subprocess.run(
            ["bash", "-n"], input=command, text=True, check=True, capture_output=True
        )


if __name__ == "__main__":
    unittest.main()
