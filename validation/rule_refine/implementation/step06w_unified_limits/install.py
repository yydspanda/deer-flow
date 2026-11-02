"""Install reviewed small files after offline evidence and isolated local tests."""

import fcntl
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import types
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 2 * 1024 * 1024
TEST_TIMEOUT_SECONDS = 180
EVIDENCE_HASHES = {
    "response_sha256": "fb93fa57d6238a83094cd28d275eb59a42e156dc04617c93cd76482cbb84834b",
    "original_prompt_sha256": "7a8e9582b12890578e1cb3ce9593d30391343614ec2474273b5c362952a82484",
}
EVIDENCE_EXPECTED = {
    "check": "historical_numeric_equal_replay",
    "status": "passed",
    "execution_mode": "offline_replay",
    "material_source": "historical_export",
    "response_origin": "saved_model_response",
    "analysis_status": "proposal",
    "new_format_valid": True,
    "comparison_version": "strict_strings_and_numeric_equal_v1",
    "preview_materials_used": False,
    "new_model_request_sent": False,
    "application_code_updated": False,
    "automatic_apply": False,
    "formal_recommendation_submitted": False,
    "original_report_unchanged": True,
    "original_prompt_preserved": True,
    "model_calls": 0,
    "sdk_request_calls": 0,
    "redis_calls": 0,
    "database_calls": 0,
    "business_alerts_submitted": 0,
}


class InstallError(ValueError):
    """Fixed, payload-free diagnostic codes suitable for command output."""

    def __init__(self, code, *, relative_file=None, actual_sha256=None):
        super().__init__(code)
        self.relative_file = relative_file
        self.actual_sha256 = actual_sha256


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative(value):
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise InstallError("invalid_manifest_path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(p in {".", ".."} for p in path.parts)
        or str(path) != value
    ):
        raise InstallError("invalid_manifest_path")
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise InstallError("invalid_manifest_hash")
    return value


def _path(root, relative):
    path = root
    if root.is_symlink() or not root.is_dir():
        raise InstallError("invalid_project_directory")
    for part in PurePosixPath(_relative(relative)).parts:
        path = path / part
        if path.is_symlink():
            raise InstallError("symlink_not_allowed")
    return path


def _read(root, relative):
    path = _path(root, relative)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise InstallError("regular_file_required")
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise InstallError("file_over_budget")
    return raw, stat.S_IMODE(info.st_mode), (info.st_dev, info.st_ino)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstallError("duplicate_evidence_field")
        result[key] = value
    return result


def verify_evidence(directory, packet):
    directory = Path(directory).absolute()
    found = _read(directory, "summary.json")
    if found is None:
        raise InstallError("offline_evidence_missing")
    raw = found[0]
    evidence = json.loads(raw, object_pairs_hook=_unique)
    hashes = packet.get("evidence_expectations", EVIDENCE_HASHES)
    if hashes != EVIDENCE_HASHES:
        raise InstallError("unexpected_evidence_expectations")
    expected = {**EVIDENCE_EXPECTED, **hashes}
    if not isinstance(evidence, dict) or any(
        type(evidence.get(key)) is not type(value) or evidence.get(key) != value
        for key, value in expected.items()
    ):
        raise InstallError("offline_evidence_mismatch")
    return {"summary_sha256": digest(raw), "verified_fields": expected}


def validate_packet(packet):
    files, dependencies = packet.get("files"), packet.get("dependencies")
    test_files, tests = packet.get("test_files", {}), packet.get("tests")
    if not isinstance(files, dict) or not 1 <= len(files) <= 128:
        raise InstallError("invalid_file_manifest")
    if not isinstance(dependencies, dict) or len(dependencies) > 128:
        raise InstallError("invalid_dependency_manifest")
    if not isinstance(test_files, dict) or len(test_files) > 128:
        raise InstallError("invalid_test_manifest")
    if (
        not isinstance(tests, list)
        or not 1 <= len(tests) <= 32
        or len(set(tests)) != len(tests)
    ):
        raise InstallError("invalid_test_entries")
    if set(files) & set(dependencies) or set(test_files) & (
        set(files) | set(dependencies)
    ):
        raise InstallError("manifest_path_collision")
    staged = {}
    for relative, entry in files.items():
        _relative(relative)
        if not isinstance(entry, dict) or not isinstance(entry.get("content"), str):
            raise InstallError("invalid_file_manifest")
        previous = entry.get("old_sha256")
        if previous is not None:
            _sha(previous)
        alternatives = entry.get("accepted_previous_sha256", [])
        if not isinstance(alternatives, list):
            raise InstallError("invalid_previous_versions")
        for value in alternatives:
            if value is not None:
                _sha(value)
        raw = entry["content"].encode("utf-8")
        if len(raw) > MAX_FILE_BYTES or digest(raw) != _sha(entry.get("new_sha256")):
            raise InstallError("package_content_mismatch")
        staged[relative] = raw
    for relative, expected in dependencies.items():
        _relative(relative)
        _sha(expected)
    for relative, source in test_files.items():
        _relative(relative)
        if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_FILE_BYTES:
            raise InstallError("invalid_test_content")
        staged[relative] = source.encode("utf-8")
    for relative in tests:
        _relative(relative)
        if relative not in staged or not relative.endswith(".py"):
            raise InstallError("test_entry_not_packaged")
    for relative, raw in staged.items():
        if relative.endswith(".py"):
            compile(raw, "<packaged-source>", "exec")
    return staged


def _fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(path.parent)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8")


@contextmanager
def _lock(root):
    path = _path(root, ".rule-refine-numeric-budget-install.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InstallError("invalid_install_lock")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("another_installer_running") from None
        yield


_TEST_RUNNER = """import runpy, sys
def guard(event, args):
    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo', 'sqlite3.connect', 'subprocess.Popen', 'os.system', 'os.exec'}:
        raise RuntimeError('service_operation_forbidden_in_install_tests')
sys.addaudithook(guard)
entry = sys.argv[1]
sys.argv = [entry]
runpy.run_path(entry, run_name='__main__')
"""


def run_checks(staged, tests):
    total = 0
    deadline = time.monotonic() + TEST_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="numeric-budget-tests-") as temporary:
        stage = Path(temporary)
        for relative, raw in staged.items():
            _save(stage / relative, raw)
        for relative in tests:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InstallError("isolated_tests_timed_out")
            # Both streams remain private; unittest failures may contain source values.
            with tempfile.TemporaryFile() as output:
                try:
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            "-B",
                            "-c",
                            _TEST_RUNNER,
                            str(stage / relative),
                        ],
                        cwd=stage,
                        stdout=output,
                        stderr=output,
                        timeout=remaining,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    raise InstallError("isolated_tests_timed_out") from None
                output.seek(0)
                raw = output.read(MAX_FILE_BYTES + 1)
            counts = re.findall(rb"^Ran (\d+) tests? in ", raw, re.MULTILINE)
            if (
                result.returncode
                or len(raw) > MAX_FILE_BYTES
                or len(counts) != 1
                or int(counts[0]) < 1
            ):
                raise InstallError("isolated_tests_failed", relative_file=relative)
            total += int(counts[0])
    return total


def _same(found, original):
    return (
        found is None
        if original is None
        else found is not None and found[0] == original[0] and found[2] == original[2]
    )


def _replace(root, relative, raw, expected, mode, committed):
    path = _path(root, relative)
    if not _same(_read(root, relative), expected):
        raise InstallError("concurrent_file_change")
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    _path(root, relative)
    fd, temporary = tempfile.mkstemp(prefix=".numeric-budget-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
        if not _same(_read(root, relative), expected):
            raise InstallError("concurrent_file_change")
        if expected is None:
            # link is an atomic, no-replace publication for genuinely new files.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        committed((info.st_dev, info.st_ino))
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _order(relative):
    name = PurePosixPath(relative).name
    return (
        {
            "limits.py": 0,
            "condition_values.py": 0,
            "contracts.py": 0,
            "filter_evaluation.py": 1,
            "analysis.py": 3,
            "background_service.py": 4,
        }.get(name, 2),
        relative,
    )


def _rollback(root, written, originals, files):
    restored, retained = [], []
    for relative, created_identity in reversed(written):
        try:
            found = _read(root, relative)
            if found is None or digest(found[0]) != files[relative]["new_sha256"]:
                retained.append(relative)
                continue
            before = originals[relative]
            if before is None:
                if found[2] != created_identity:
                    retained.append(relative)
                    continue
                _path(root, relative).unlink()
                _fsync_directory((root / relative).parent)
            else:
                _replace(
                    root, relative, before[0], found, before[1], lambda identity: None
                )
            restored.append(relative)
        except Exception:
            retained.append(relative)
    return {
        "restored_files": restored,
        "retained_concurrent_or_unavailable_files": retained,
    }


def _preserve_configuration(root, packet):
    relative = "config/rule_refine.limits.local.json"
    if relative not in packet["files"]:
        return packet, None
    module_name = "_rule_refine_installer_limits"
    module = types.ModuleType(module_name)
    module.__file__ = str(root / "my_workflows/zeus/rule_refine/limits.py")
    sys.modules[module_name] = module
    try:
        exec(
            compile(
                packet["files"]["my_workflows/zeus/rule_refine/limits.py"]["content"],
                "<reviewed-limits>",
                "exec",
            ),
            module.__dict__,
        )
        found = _read(root, relative)
        effective = module.get_limits(root)
    except Exception:
        raise InstallError(
            "limits_configuration_invalid", relative_file=relative
        ) from None
    finally:
        sys.modules.pop(module_name, None)
    if found is not None:
        packet = copy.deepcopy(packet)
        packet["files"][relative] = {
            "content": found[0].decode("utf-8"),
            "old_sha256": digest(found[0]),
            "new_sha256": digest(found[0]),
        }
    return packet, {
        name: getattr(effective, name)
        for name in (
            "input_kib",
            "output_kib",
            "timeout_seconds",
            "retry_attempts",
            "retry_delay_seconds",
        )
    }


def apply(root, evidence_directory, packet):
    root = Path(root).absolute()
    staged = validate_packet(packet)
    evidence = (
        verify_evidence(evidence_directory, packet)
        if packet.get("require_evidence", True)
        else {"basis": "reviewed_source_versions_and_isolated_tests"}
    )
    with _lock(root):
        packet, effective_limits = _preserve_configuration(root, packet)
        staged = validate_packet(packet)
        originals = {}
        for relative, entry in packet["files"].items():
            found = _read(root, relative)
            current = digest(found[0]) if found is not None else None
            allowed = {
                entry.get("old_sha256"),
                entry["new_sha256"],
                *entry.get("accepted_previous_sha256", []),
            }
            if current not in allowed:
                raise InstallError(
                    "unknown_project_file_version",
                    relative_file=relative,
                    actual_sha256=current,
                )
            originals[relative] = found
        dependencies = {}
        for relative, expected in packet["dependencies"].items():
            found = _read(root, relative)
            if found is None or digest(found[0]) != expected:
                raise InstallError(
                    "dependency_version_mismatch",
                    relative_file=relative,
                    actual_sha256=digest(found[0]) if found is not None else None,
                )
            dependencies[relative] = found
            staged[relative] = found[0]
        # Test defaults in an isolated tree; the operator's custom policy was
        # validated above and is preserved, not copied over the test fixtures.
        check_sources = {
            name: raw
            for name, raw in staged.items()
            if name != "config/rule_refine.limits.local.json"
        }
        test_count = run_checks(check_sources, packet["tests"])
        for relative, before in {**originals, **dependencies}.items():
            if not _same(_read(root, relative), before):
                raise InstallError("project_changed_during_tests")
        changed = sorted(
            (
                relative
                for relative, before in originals.items()
                if before is None
                or digest(before[0]) != packet["files"][relative]["new_sha256"]
            ),
            key=_order,
        )
        report = {
            "check": "rule_refine_unified_limits_install",
            "status": "already_installed",
            "tests_passed": test_count,
            "files_changed": 0,
            "application_code_updated": False,
            "installed_state_verified": True,
            "restart_required": True,
            "automatic_restart": False,
            "automatic_apply": False,
            "model_calls": 0,
            "redis_calls": 0,
            "database_calls": 0,
            "business_alerts_submitted": 0,
        }
        if effective_limits is not None:
            report["limits"] = effective_limits
            report["configuration_file"] = str(
                root / "config/rule_refine.limits.local.json"
            )
        if not changed:
            return report
        data_directory = Path(evidence_directory).absolute()
        parent = (
            data_directory.parent
            if packet.get("require_evidence", True)
            else data_directory
        )
        if parent.is_symlink():
            raise InstallError("symlink_not_allowed")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="unified-limits-install-", dir=parent))
        for relative in changed:
            if originals[relative] is not None:
                _save(backup / "before" / relative, originals[relative][0])
        _save(
            backup / "manifest.json",
            _json(
                {
                    "evidence": evidence,
                    "tests_passed": test_count,
                    "files": [
                        {
                            "path": relative,
                            "before_sha256": digest(originals[relative][0])
                            if originals[relative]
                            else None,
                            "new_sha256": packet["files"][relative]["new_sha256"],
                        }
                        for relative in changed
                    ],
                }
            ),
        )
        written = []
        try:
            for relative in changed:
                before = originals[relative]
                _replace(
                    root,
                    relative,
                    staged[relative],
                    before,
                    before[1] if before else 0o644,
                    lambda identity, name=relative: written.append((name, identity)),
                )
            report.update(
                status="installed",
                files_changed=len(changed),
                application_code_updated=True,
                backup_dir=str(backup),
            )
            _save(backup / "result.json", _json(report))
        except Exception:
            rollback = _rollback(root, written, originals, packet["files"])
            try:
                _save(backup / "rollback.json", _json(rollback))
            except OSError:
                pass
            code = (
                "installation_failed_rollback_incomplete"
                if rollback["retained_concurrent_or_unavailable_files"]
                else "installation_failed_rolled_back"
            )
            raise InstallError(code) from None
        return report
