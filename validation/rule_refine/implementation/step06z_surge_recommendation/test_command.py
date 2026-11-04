"""Verify the copyable command executes exactly its packaged source, offline."""

import ast
import sys
import unittest
from unittest.mock import patch

import build_command


class CommandTests(unittest.TestCase):
    def test_packaged_modules_import_and_entry_receives_hashes(self):
        packet = build_command.contents()
        packet["files"]["surge_runner"] += """
def main(*, tool_hashes=None):
    assert set(tool_hashes) == {'context_budget', 'historical_adapter', 'surge_materials', 'ai_review', 'sampling', 'surge_runner'}
    assert all(len(value) == 64 for value in tool_hashes.values())
    return 17
"""
        with patch.dict(sys.modules):
            with self.assertRaises(SystemExit) as result:
                exec(build_command.runtime_source(packet), {})
        self.assertEqual(result.exception.code, 17)

    def test_all_sources_parse_on_python_311(self):
        for name, source in (
            build_command.contents()["files"]
            | build_command.replay_contents()["files"]
            | build_command.resume_contents()["files"]
            | build_command.final_replay_contents()["files"]
            | build_command.independent_contents()["files"]
        ).items():
            with self.subTest(module=name):
                ast.parse(source, feature_version=(3, 11))

    def test_offline_packet_has_no_live_modules_and_dispatches_replay(self):
        packet = build_command.replay_contents()
        self.assertEqual(set(packet["files"]), {"ai_review", "saved_generation"})
        packet["files"]["saved_generation"] += """
def main(*, tool_hashes=None):
    assert set(tool_hashes) == {'ai_review', 'saved_generation'}
    return 19
"""
        with patch.dict(sys.modules), self.assertRaises(SystemExit) as result:
            exec(build_command.runtime_source(packet, offline=True), {})
        self.assertEqual(result.exception.code, 19)

    def test_unknown_module_cannot_be_silently_packaged(self):
        packet = build_command.contents()
        packet["files"]["unexpected"] = "raise RuntimeError('do not execute')"
        with self.assertRaisesRegex(ValueError, "unexpected_tool_modules"):
            build_command.runtime_source(packet)

    def test_saved_review_packet_only_loads_offline_modules(self):
        packet = build_command.final_replay_contents()
        self.assertEqual(
            set(packet["files"]), {"ai_review", "saved_generation", "saved_review"}
        )
        packet["files"]["saved_review"] += """
def main(*, tool_hashes=None):
    assert set(tool_hashes) == {'ai_review', 'saved_generation', 'saved_review'}
    return 29
"""
        with patch.dict(sys.modules), self.assertRaises(SystemExit) as result:
            exec(build_command.runtime_source(packet, final_replay=True), {})
        self.assertEqual(result.exception.code, 29)

    def test_resume_packet_dispatches_only_resume_entry(self):
        packet = build_command.resume_contents()
        packet["files"]["surge_runner"] += (
            "\ndef main(**kwargs): raise AssertionError('must not regenerate')\n"
        )
        packet["files"]["resume_review"] += """
def main(*, tool_hashes=None):
    assert set(tool_hashes) == {'context_budget', 'historical_adapter', 'surge_materials', 'ai_review', 'sampling', 'surge_runner', 'saved_generation', 'saved_review', 'resume_review'}
    return 23
"""
        with patch.dict(sys.modules), self.assertRaises(SystemExit) as result:
            exec(build_command.runtime_source(packet, resume=True), {})
        self.assertEqual(result.exception.code, 23)

    def test_new_prompt_entry_reuses_saved_review_samples(self):
        packet = build_command.resume_contents()
        packet["files"]["surge_runner"] += (
            "\ndef main(**kwargs): raise AssertionError('must not regenerate')\n"
        )
        packet["files"]["resume_review"] += """
def main(*, tool_hashes=None, reuse_review_samples=False):
    assert reuse_review_samples is True
    return 31
"""
        with patch.dict(sys.modules), self.assertRaises(SystemExit) as result:
            exec(build_command.runtime_source(packet, rereview=True), {})
        self.assertEqual(result.exception.code, 31)

    def test_loader_integrity_checked_before_imports(self):
        source = build_command.runtime_source(build_command.contents())
        source = source.replace(
            "decoder = zlib.decompressobj()",
            'payload = "invalid"\ndecoder = zlib.decompressobj()',
        )
        with patch.dict(sys.modules):
            with self.assertRaises(Exception):
                exec(source, {})

    def test_independent_entry_never_dispatches_draft_review_or_full_chain(self):
        packet = build_command.independent_contents()
        for module in ("surge_runner", "resume_review"):
            packet["files"][module] += (
                "\ndef main(**kwargs): raise AssertionError('wrong entry')\n"
            )
        packet["files"]["independent_assessment"] += """
def main(*, tool_hashes=None):
    assert 'independent_assessment' in tool_hashes
    return 37
"""
        with patch.dict(sys.modules), self.assertRaises(SystemExit) as result:
            exec(build_command.runtime_source(packet, independent=True), {})
        self.assertEqual(result.exception.code, 37)

    def test_independent_mode_cannot_be_combined_with_review(self):
        with self.assertRaisesRegex(ValueError, "conflicting_tool_modes"):
            build_command.runtime_source(
                build_command.independent_contents(), independent=True, rereview=True
            )

    def test_context_route_script_embeds_only_readonly_diagnostic(self):
        command = build_command.context_route_command()
        source = command.split("<<'PY_CONTEXT_ROUTE_CHECK'\n", 1)[1].split(
            "\nPY_CONTEXT_ROUTE_CHECK\n", 1
        )[0]
        ast.parse(source, feature_version=(3, 11))
        self.assertEqual(
            source,
            (build_command.HERE / "context_route_check.py").read_text(encoding="utf-8"),
        )
        self.assertNotIn("SOC_INDEPENDENT_AI_ASSESSMENT", command)

    def test_upstream_metadata_script_embeds_only_readonly_diagnostic(self):
        command = build_command.upstream_metadata_command()
        source = command.split("<<'PY_UPSTREAM_CONTEXT_CHECK'\n", 1)[1].split(
            "\nPY_UPSTREAM_CONTEXT_CHECK\n", 1
        )[0]
        ast.parse(source, feature_version=(3, 11))
        self.assertEqual(
            source,
            (build_command.HERE / "upstream_metadata_check.py").read_text(
                encoding="utf-8"
            ),
        )
        self.assertNotIn("SOC_INDEPENDENT_AI_ASSESSMENT", command)


if __name__ == "__main__":
    unittest.main()
