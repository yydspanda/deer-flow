"""Offline tests for source transforms; never run legacy launch/cleanup code."""

from __future__ import annotations
import ast
import configparser
import hashlib
import os
from pathlib import Path
import re
import subprocess
from zipfile import ZipFile
import unittest
import tempfile
from launcher_transforms import (
    LOCAL_CAPACITY_COMMENT,
    SUPPORTED_PATHS,
    transforms,
)


PUB = "Pub_DeepSeek_V4_Flash"
RETIRED = ("DeepSeek_V32", "DeepSeek_V4_Flash_My")
ZIP_PATH = (
    None
    if os.environ.get("SEC_KNOW_SOURCE_ROOT")
    else Path(__file__).resolve().parents[3] / "sec_know_model.zip"
)


def _read_root_source(root: Path, path: str) -> str:
    target = root / path
    if not target.is_file():
        raise ValueError("SEC_KNOW_SOURCE_ROOT lacks a required target")
    return target.read_text(encoding="utf-8")


def _check(condition: bool, message: str) -> None:
    """Do not let assertion rewriting render private source or command arguments."""
    if not condition:
        raise AssertionError(message)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sections(source: str) -> dict[str, str]:
    markers = list(re.finditer("^\\[([^\\]\\r\\n]+)\\].*$", source, re.MULTILINE))
    result = {"__prefix__": source[: markers[0].start()]}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(source)
        result[marker.group(1)] = source[marker.start() : end]
    return result


def _literal(source: str, name: str):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            (isinstance(t, ast.Name) and t.id == name for t in node.targets)
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing assignment {name}")


class LauncherTransformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_root = os.environ.get("SEC_KNOW_SOURCE_ROOT")
        if source_root is not None:
            root = Path(source_root)
            if not root.is_dir():
                raise ValueError("SEC_KNOW_SOURCE_ROOT is not an existing directory")
            cls.original = staticmethod(lambda path: _read_root_source(root, path))
            return
        if not ZIP_PATH.is_file():
            raise ValueError(
                "Reviewed source ZIP is unavailable; set SEC_KNOW_SOURCE_ROOT"
            )
        archive = ZipFile(ZIP_PATH)
        cls.addClassCleanup(archive.close)

        def read(path):
            if path not in archive.namelist():
                raise ValueError("Reviewed source ZIP lacks a required target")
            return archive.read(path).decode("utf-8")

        cls.original = staticmethod(read)

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="queue-launcher-test-")
        self.addCleanup(directory.cleanup)
        self.tmp_path = Path(directory.name)

    def test_supervisor_removes_complete_old_sections_and_only_changes_pub_capacity(
        self,
    ):
        original = self.original
        for case_index, path in enumerate(
            ["config/celery_task.conf", "config/celery_task.detector.conf"]
        ):
            with self.subTest(case=case_index):
                source = original(path)
                changed = transforms(path, source)
                before = _sections(source)
                after = _sections(changed)
                self.assertTrue(
                    set(after)
                    == set(before) - {f"program:{name}_worker" for name in RETIRED},
                    "Launcher transform invariant failed (source suppressed)",
                )
                for section, block in after.items():
                    expected = before[section]
                    if section == f"program:{PUB}_worker":
                        expected = expected.replace("worker -c 15 ", "worker -c 27 ", 1)
                    self.assertTrue(_digest(block) == _digest(expected), section)
                config = configparser.ConfigParser(interpolation=None)
                config.read_string(changed)
                _check(
                    " -c 27 " in config[f"program:{PUB}_worker"]["command"],
                    "Pub production concurrency differs",
                )
                for name in RETIRED:
                    _check(name not in changed, "Retired model remains in launcher")

    def test_local_launchers_preserve_two_slots_and_all_other_commands(self):
        original = self.original
        tmp_path = self.tmp_path
        for case_index, path in enumerate(["local_run.sh", "local_run.detector.sh"]):
            with self.subTest(case=case_index):
                source = original(path)
                changed = transforms(path, source)
                expected = "".join(
                    (
                        line
                        for line in source.splitlines(keepends=True)
                        if not any((f"-Q {name}_queue " in line for name in RETIRED))
                    )
                )
                without_comment = changed.replace(LOCAL_CAPACITY_COMMENT + "\n", "", 1)
                self.assertTrue(
                    _digest(without_comment) == _digest(expected),
                    "Launcher transform invariant failed (source suppressed)",
                )
                _check(
                    changed.count(LOCAL_CAPACITY_COMMENT) == 1,
                    "LOCAL capacity comment differs",
                )
                pub_lines = [
                    line for line in changed.splitlines() if f"-Q {PUB}_queue " in line
                ]
                self.assertTrue(
                    len(pub_lines) == 1,
                    "Launcher transform invariant failed (source suppressed)",
                )
                _check(" -c 2 " in pub_lines[0], "LOCAL Pub concurrency changed")
                _check(" -P threads " in pub_lines[0], "LOCAL pool changed")
                script = tmp_path / path
                script.write_text(changed, encoding="utf-8")
                result = subprocess.run(
                    ["bash", "-n", str(script)], capture_output=True, check=False
                )
                self.assertTrue(
                    result.returncode == 0, "Shell syntax failed (source suppressed)"
                )
                for name in RETIRED:
                    _check(
                        name not in changed, "Retired model remains in LOCAL launcher"
                    )

    def test_stg_tools_and_shared_detector_are_outside_patch_scope(self):
        for path in (
            "scripts/detector_worker.py",
            "test/test_all_models.py",
            "util/diagnose_model_state.py",
            "validation/verify_stg_distribution.py",
        ):
            with self.subTest(path=path):
                self.assertNotIn(path, SUPPORTED_PATHS)
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    transforms(path, "source is intentionally not read")

    def test_cleanup_only_changes_queue_catalog_and_obsolete_comments(self):
        original = self.original
        path = "validation/cleanup_priority9_tasks.py"
        source = original(path)
        changed = transforms(path, source)
        self.assertTrue(
            _literal(changed, "ENV_QUEUES")
            == {
                "local": [f"{PUB}_queue"],
                "stg": _literal(source, "ENV_QUEUES")["stg"],
                "prd": [f"{PUB}_queue"],
            },
            "Launcher transform invariant failed (source suppressed)",
        )
        old_tree, new_tree = (ast.parse(source), ast.parse(changed))
        for tree in (old_tree, new_tree):
            tree.body = [
                node
                for node in tree.body
                if not (
                    isinstance(node, ast.Assign)
                    and any(
                        (
                            isinstance(t, ast.Name) and t.id == "ENV_QUEUES"
                            for t in node.targets
                        )
                    )
                )
            ]
        self.assertTrue(
            _digest(ast.dump(old_tree)) == _digest(ast.dump(new_tree)),
            "Launcher transform invariant failed (source suppressed)",
        )
        stg_lines_before = [
            line for line in source.splitlines(keepends=True)
            if '"stg":' in line or line.startswith("# stg:")
        ]
        stg_lines_after = [
            line for line in changed.splitlines(keepends=True)
            if '"stg":' in line or line.startswith("# stg:")
        ]
        self.assertTrue(
            _digest("".join(stg_lines_before)) == _digest("".join(stg_lines_after)),
            "STG cleanup mapping/comment must stay byte-for-byte unchanged",
        )

    def test_diagnostic_manual_updates_only_target_list_and_capacity_comments(self):
        original = self.original
        tmp_path = self.tmp_path
        path = "my_workflows/util/check_celery_queue.sh"
        source = original(path)
        changed = transforms(path, source)
        old_commands = [
            line for line in source.splitlines() if line and (not line.startswith("#"))
        ]
        new_commands = [
            line for line in changed.splitlines() if line and (not line.startswith("#"))
        ]
        expected = [
            line.replace(
                'ms=[\\"DeepSeek_V32\\",\\"DeepSeek_V4_Flash_My\\",\\"Pub_DeepSeek_V4_Flash\\"]',
                'ms=[\\"Pub_DeepSeek_V4_Flash\\"]',
            )
            for line in old_commands
        ]
        self.assertTrue(
            _digest("\n".join(new_commands)) == _digest("\n".join(expected)),
            "Launcher transform invariant failed (source suppressed)",
        )
        _check("1主 + 27子 = 28" in changed, "Diagnostic concurrency text differs")
        _check(
            "predicted 最低" not in changed, "Obsolete distribution explanation remains"
        )
        for name in RETIRED:
            _check(name not in changed, "Retired model remains in diagnostic manual")
        script = tmp_path / "check.sh"
        script.write_text(changed, encoding="utf-8")
        self.assertTrue(
            subprocess.run(
                ["bash", "-n", str(script)], capture_output=True, check=False
            ).returncode
            == 0,
            "Launcher transform invariant failed (source suppressed)",
        )

    def test_source_root_reads_required_file(self):
        tmp_path = self.tmp_path
        target = tmp_path / "config" / "example.py"
        target.parent.mkdir()
        target.write_text("synthetic = True\n", encoding="utf-8")
        self.assertTrue(
            _digest(_read_root_source(tmp_path, "config/example.py"))
            == _digest("synthetic = True\n"),
            "Launcher transform invariant failed (source suppressed)",
        )

    def test_source_root_missing_target_fails_instead_of_skipping(self):
        tmp_path = self.tmp_path
        with self.assertRaisesRegex(ValueError, "required target"):
            _read_root_source(tmp_path, "missing.py")

    def test_all_changed_python_sources_parse_without_importing_private_app(self):
        original = self.original
        for case_index, path in enumerate(sorted(SUPPORTED_PATHS)):
            with self.subTest(case=case_index):
                changed = transforms(path, original(path))
                if path.endswith(".py"):
                    ast.parse(changed)

    def test_unknown_file_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            transforms("run.sh", "source")

    def test_unrecognized_source_is_rejected_instead_of_partially_patched(self):
        for case_index, path in enumerate(
            [
                "config/celery_task.conf",
                "config/celery_task.detector.conf",
                "local_run.sh",
                "local_run.detector.sh",
                "validation/cleanup_priority9_tasks.py",
                "my_workflows/util/check_celery_queue.sh",
            ]
        ):
            with self.subTest(case=case_index):
                with self.assertRaises(ValueError):
                    transforms(path, "unknown source\n")


if __name__ == "__main__":
    unittest.main()
