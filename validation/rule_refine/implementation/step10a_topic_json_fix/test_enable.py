"""Run the frozen activation tests with this release's installed source hashes."""

import types
import unittest

import build_command as builder

legacy = builder.load(
    "topic_json_frozen_activation_tests", builder.PREVIOUS / "test_enable.py"
)
legacy.BUILDER = builder
legacy.INSTALLER = types.ModuleType("topic_json_enable_test_installer")
exec(
    compile(builder.installer_source(), "<installer>", "exec"),
    legacy.INSTALLER.__dict__,
)


class UpdatedSourceEnableTests(legacy.EnableTests):
    def test_previous_activation_hashes_reject_new_runtime(self):
        previous = builder.PREV.baseline()
        previous.update(
            {
                name: entry["content"].encode("utf-8")
                for name, entry in builder.PREV.contents()["files"].items()
            }
        )
        old_expected = {
            name: builder.BASE.sha(raw)
            for name, raw in previous.items()
            if not name.startswith(builder.TEST)
        }
        with self.assertRaisesRegex(
            legacy.INSTALLER.InstallError, "topic_installation_version_mismatch"
        ):
            legacy.ENABLE.configure(
                self.root,
                self.backups,
                {"env_profile": "LOCAL"},
                old_expected,
                legacy.INSTALLER,
            )
        self.assertFalse((self.root / legacy.ENABLE.CONFIG).exists())
        self.assertEqual((self.root / "run.sh").read_bytes(), self.run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
