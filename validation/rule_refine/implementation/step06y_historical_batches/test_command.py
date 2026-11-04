import ast
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import build_command


class CommandTests(unittest.TestCase):
    def test_embedded_sources_are_exact_and_python311_compatible(self):
        packet = build_command.contents()
        source = build_command.runtime_source(packet)
        encoded = source.split('payload = """', 1)[1].split('"""', 1)[0]
        raw = zlib.decompress(base64.b64decode(encoded))
        self.assertEqual(json.loads(raw), packet)
        self.assertIn(hashlib.sha256(raw).hexdigest(), source)
        self.assertEqual(len(packet["pins"]), 13)
        self.assertEqual(set(packet["files"]), set(build_command.ORDER))
        for body in [source, *packet["files"].values()]:
            ast.parse(body, feature_version=(3, 11))

    def test_complete_command_has_valid_bash_syntax_and_is_reproducible(self):
        command = build_command.build()
        syntax = subprocess.run(
            ["bash", "-n"], input=command, text=True, capture_output=True
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertEqual(command, build_command.build())
        self.assertIn('cd "$HOME/sec_know_model"', command)
        self.assertNotIn("pip install", command)

    def test_nonlocal_exits_before_application_import_or_directory_creation(self):
        source = build_command.runtime_source(build_command.contents())
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-B", "-c", source],
                cwd=temporary,
                env={**os.environ, "env_profile": "PRD", "ENV_PROFILE": "PRD"},
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["error_code"], "local_only")
            self.assertEqual(summary["model_calls"], 0)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_corrupt_packet_stops_before_any_tool_import(self):
        packet = build_command.contents()
        source = build_command.runtime_source(packet)
        encoded = source.split('payload = """', 1)[1].split('"""', 1)[0]
        digest = hashlib.sha256(zlib.decompress(base64.b64decode(encoded))).hexdigest()
        corrupted = source.replace(digest, "0" * 64)
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-B", "-c", corrupted],
                cwd=temporary,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("工具校验失败", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
