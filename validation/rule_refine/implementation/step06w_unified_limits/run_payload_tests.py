"""Assemble reviewed sources and run deterministic tests in a temporary tree."""

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("unified_rule_builder", HERE / "build_command.py")
installer = load("unified_rule_installer", HERE / "install.py")


def assemble(root, packet):
    for relative, entry in packet["files"].items():
        if relative == builder.POLICY:
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry["content"], encoding="utf-8")
    for relative, expected in packet["dependencies"].items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(builder.find_source(relative, expected).read_bytes())


def main():
    packet = builder.contents()
    tests = packet["tests"] + [builder.TEST + "test_task_source.py"]
    if len(sys.argv) > 1:
        names = set(sys.argv[1:])
        tests = [name for name in tests if Path(name).name in names]
    failures = []
    with tempfile.TemporaryDirectory(prefix="rule-unified-tests-") as temporary:
        root = Path(temporary)
        assemble(root, packet)
        for entry in tests:
            runner = installer._TEST_RUNNER
            if entry.endswith("test_task_source.py"):
                runner = runner.replace("'sqlite3.connect', ", "")
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", runner, str(root / entry)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            print(
                Path(entry).name,
                "PASS" if result.returncode == 0 else "FAIL",
                flush=True,
            )
            print(
                result.stderr[-10000:]
                if result.returncode
                else "\n".join(result.stderr.splitlines()[-4:]),
                flush=True,
            )
            if result.returncode:
                failures.append(entry)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
