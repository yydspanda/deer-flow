"""Assemble exact runtime sources and run the installer's guarded offline suite."""

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("surge_runtime_builder", HERE / "build_command.py")
installer = load("surge_runtime_installer", HERE / "install.py")


def staged_sources(packet):
    staged = installer.validate_packet(packet)
    staged.pop(builder.POLICY, None)
    staged.update(
        {
            relative: builder.find_source(relative, expected).read_bytes()
            for relative, expected in packet["dependencies"].items()
        }
    )
    return staged


def assemble(root, packet):
    for relative, raw in staged_sources(packet).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def main():
    packet = builder.contents()
    selected = set(sys.argv[1:])
    tests = [
        name for name in packet["tests"] if not selected or Path(name).name in selected
    ]
    if not tests:
        raise SystemExit("No matching test entries")
    staged = staged_sources(packet)
    count = 0
    for entry in tests:
        try:
            number = installer.run_checks(staged, [entry])
        except installer.InstallError as error:
            print(Path(entry).name, "FAIL", str(error), flush=True)
            return 1
        count += number
        print(Path(entry).name, "PASS", number, flush=True)
    print("Offline tests passed:", count, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
