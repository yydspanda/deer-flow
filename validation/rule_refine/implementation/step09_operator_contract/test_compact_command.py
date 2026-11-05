"""Delta reconstruction and exact-byte/idempotence boundaries; no live calls."""

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compact = load("operator_compact_test", HERE / "compact_command.py")
fixture = load("operator_delivery_fixture", HERE / "test_delivery.py")
installer = fixture.INSTALLER


class CompactCommandTests(unittest.TestCase):
    def test_inline_profile_omits_only_the_frozen_synthetic_test(self):
        full = fixture.BUILDER.contents()
        before = copy.deepcopy(full)
        inline = compact.inline_packet(full)
        self.assertEqual(full, before)
        self.assertEqual(
            set(full["files"]) - set(inline["files"]), {compact.INLINE_OMISSION}
        )
        self.assertEqual(
            set(full["tests"]) - set(inline["tests"]), {compact.INLINE_OMISSION}
        )
        self.assertEqual(inline["test_files"], full["test_files"])
        self.assertEqual(inline["dependencies"], full["dependencies"])
        for name, entry in inline["files"].items():
            self.assertEqual(entry, full["files"][name])
        self.assertEqual(
            inline["local_and_full_package_only_tests"], [compact.INLINE_OMISSION]
        )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="operator-compact-test-")
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.before = b'# synthetic source\nVALUE = "before"\n'
        self.packet = fixture.packet(self.before)
        self.dependency = fixture.BUILDER.MODULE + "dependency.py"
        self.packet["dependencies"][self.dependency] = fixture.BUILDER.sha(
            b"# dependency\n"
        )
        self.bundle = compact.make_bundle(
            self.packet,
            {fixture.OLD_FILE: self.before},
            fixture.BUILDER.installer_source(),
        )
        self.write(fixture.OLD_FILE, self.before)
        self.write(fixture.TEST_FILE, fixture.TEST_SOURCE.encode("utf-8"))
        self.write(self.dependency, b"# dependency\n")

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, raw):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def after(self, relative):
        return self.packet["files"][relative]["content"].encode("utf-8")

    def reconstruct(self, bundle=None):
        return compact.reconstruct(
            self.bundle if bundle is None else bundle, self.root, installer
        )

    def test_old_new_and_mixed_states_reconstruct_identical_packet_without_writes(self):
        for names in ((), (fixture.OLD_FILE,), (fixture.OLD_FILE, fixture.NEW_FILE)):
            for name in names:
                self.write(name, self.after(name))
            original = {
                p.relative_to(self.root): p.read_bytes()
                for p in self.root.rglob("*")
                if p.is_file()
            }
            self.assertEqual(self.reconstruct(), self.packet)
            self.assertEqual(
                {
                    p.relative_to(self.root): p.read_bytes()
                    for p in self.root.rglob("*")
                    if p.is_file()
                },
                original,
            )

    def test_reconstructed_packet_uses_existing_install_backup_and_idempotence(self):
        result = installer.apply(
            self.root, Path(self.temporary.name) / "data", self.reconstruct()
        )
        self.assertEqual(result["status"], "installed")
        self.assertEqual(
            (Path(result["backup_dir"]) / "before" / fixture.OLD_FILE).read_bytes(),
            self.before,
        )
        second = installer.apply(
            self.root, Path(self.temporary.name) / "data", self.reconstruct()
        )
        self.assertEqual(second["status"], "already_installed")

    def test_modified_source_and_existing_unrecognized_new_file_fail(self):
        for name in (fixture.OLD_FILE, fixture.NEW_FILE):
            with self.subTest(file=name):
                self.write(name, b"# unexpected change\n")
                with self.assertRaisesRegex(
                    installer.InstallError, "unknown_project_file_version"
                ):
                    self.reconstruct()
                (self.root / name).unlink()
                self.write(fixture.OLD_FILE, self.before)

    def test_unmodified_test_fixture_and_dependency_must_match_hash(self):
        for name, reason, before in (
            (
                fixture.TEST_FILE,
                "compact_test_version_mismatch",
                fixture.TEST_SOURCE.encode("utf-8"),
            ),
            (self.dependency, "dependency_version_mismatch", b"# dependency\n"),
        ):
            self.write(name, b"# modified\n")
            with self.assertRaisesRegex(installer.InstallError, reason):
                self.reconstruct()
            self.write(name, before)

    def test_bad_delta_range_overlap_and_target_hash_fail_closed(self):
        for edits in (
            [[0, 9999, "x"]],
            [[1, 2, "x"], [0, 0, "y"]],
            [[True, 1, "x"]],
            [[0, 1, 7]],
        ):
            bundle = copy.deepcopy(self.bundle)
            bundle["files"][fixture.OLD_FILE]["edits"] = edits
            with self.assertRaisesRegex(
                installer.InstallError, "invalid_compact_delta"
            ):
                self.reconstruct(bundle)
        bundle = copy.deepcopy(self.bundle)
        bundle["files"][fixture.OLD_FILE]["edits"] = [[0, 1, "# changed target\n"]]
        with self.assertRaisesRegex(
            installer.InstallError, "compact_result_hash_mismatch"
        ):
            self.reconstruct(bundle)

    def test_symlink_is_rejected_before_reading(self):
        target = self.root / fixture.OLD_FILE
        external = Path(self.temporary.name) / "external.py"
        external.write_bytes(self.before)
        target.unlink()
        target.symlink_to(external)
        with self.assertRaisesRegex(installer.InstallError, "symlink_not_allowed"):
            self.reconstruct()

    def test_command_payload_roundtrip_matches_compact_bundle_and_is_guarded(self):
        import base64
        import json
        import lzma
        import re

        command = compact.packtext(self.bundle)
        encoded = re.search(r'base64.b64decode\("""(.*?)"""\)', command, re.S).group(1)
        restored = json.loads(lzma.decompress(base64.b64decode(encoded)))
        self.assertEqual(restored, self.bundle)
        self.assertIn("max_length=", command)
        self.assertIn("d.unused_data", command)
        self.assertIn("hashlib.sha256(raw).hexdigest()", command)
        self.assertNotIn("curl", command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
