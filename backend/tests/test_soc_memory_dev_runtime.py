"""Exercise SOC DEV startup with recording executables and isolated existing data."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from support.shell import require_script_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
SOC_IMAGE = "deer-flow-soc-memory-gateway:sqlite-3.51.3"

_EXECUTABLE = r"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
record = {"tool": tool, "args": args, "demo": os.environ.get("SOC_DEMO_AUTH_DISABLED", "0"), "external_actions": os.environ.get("SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS"), "database": os.environ.get("SOC_DATABASE_URL")}
with Path(os.environ["SOC_TEST_TRACE"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\n")
if tool == "curl":
    sys.exit(0)
if tool == "uv":
    if args and args[0] == "sync":
        sys.exit(int(os.environ.get("SOC_TEST_SYNC_EXIT", "0")))
    if "-c" in args and any("sqlite3" in value for value in args):
        sys.exit(int(os.environ.get("SOC_TEST_SQLITE_EXIT", "0")))
    if "db" in args and "upgrade" in args:
        sys.exit(int(os.environ.get("SOC_TEST_MIGRATION_EXIT", "0")))
    sys.exit(0)
if tool == "python":
    print("Host Python must never run the SOC migration", file=sys.stderr)
    sys.exit(98)
state = Path(os.environ["SOC_TEST_BASE_IMAGE"])
if args == ["info"]:
    sys.exit(0)
if args[:2] == ["image", "inspect"]:
    sys.exit(0 if state.exists() else 1)
if args and args[0] == "build":
    def flag(*names):
        return next(args[index + 1] for index, value in enumerate(args) if value in names)
    dockerfile = Path(flag("-f", "--file"))
    content = dockerfile.read_text(encoding="utf-8")
    variables = dict(re.findall(r"^ARG\s+(\w+)=(\S+)", content, re.M))
    for index, value in enumerate(args):
        if value == "--build-arg":
            name, _, value = args[index + 1].partition("=")
            variables[name] = value
    parent = re.search(r"^FROM\s+(\S+)", content, re.M).group(1)
    parent = re.sub(r"\$\{(\w+)\}", lambda match: variables[match[1]], parent)
    if parent == flag("-t", "--tag") or parent.removesuffix(":latest") != "deer-flow-dev-gateway" or not state.exists():
        print("SOC image must derive from the separately built base image", file=sys.stderr)
        sys.exit(96)
    sys.exit(0)
if args and args[0] == "compose":
    if "build" in args:
        files = [args[index + 1] for index, value in enumerate(args) if value == "-f"]
        if any("soc-memory" in path for path in files):
            print("Base build must not load the SOC image override", file=sys.stderr)
            sys.exit(95)
        if "gateway" in args[args.index("build") + 1:]:
            state.touch()
        sys.exit(0)
    if "run" in args and "gateway" in args[args.index("run") + 1:]:
        import yaml
        checkout = Path(os.environ["SOC_TEST_BACKEND"]).parent
        overlay = yaml.safe_load((checkout / "docker/docker-compose.soc-memory-dev.yaml").read_text(encoding="utf-8"))
        environment = dict(os.environ)
        environment["SOC_DATABASE_URL"] = overlay["services"]["gateway"]["environment"]["SOC_DATABASE_URL"]
        environment["UV_EXTRAS"] = "pingan-dev"
        environment["DEER_FLOW_PROJECT_ROOT"] = str(checkout)
        command = args[args.index("gateway") + 1:]
        command = [value.replace("/app/backend", shlex.quote(os.environ["SOC_TEST_BACKEND"])).replace("/usr/local/bin/dev-entrypoint.sh", shlex.quote(str(checkout / "docker/dev-entrypoint.sh"))) for value in command]
        sys.exit(subprocess.run(command, check=False, env=environment).returncode)
    if "run" in args and "frontend" in args[args.index("run") + 1:]:
        sys.exit(0)
    if "up" in args:
        sys.exit(0)
print("Unexpected test command: " + repr(sys.argv), file=sys.stderr)
sys.exit(97)
"""


@pytest.fixture
def startup(tmp_path: Path):
    if os.name != "posix":
        pytest.skip("recording executable stubs require POSIX process execution")
    checkout = tmp_path / "checkout with spaces"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts/soc-memory-dev.sh", checkout / "scripts/soc-memory-dev.sh")
    shutil.copy2(REPO_ROOT / "scripts/detect_uv_extras.py", checkout / "scripts/detect_uv_extras.py")
    (checkout / "docker").mkdir()
    for name in ("docker-compose-dev.yaml", "docker-compose.soc-memory-dev.yaml", "Dockerfile.soc-memory-gateway", "dev-entrypoint.sh"):
        source = REPO_ROOT / "docker" / name
        if source.exists():
            shutil.copy2(source, checkout / "docker" / name)
    required = (
        "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json",
        "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite",
        "backend/soc_agent/integrations/pingan/policies/tenant-disposition-v2.json",
        "backend/soc_agent/integrations/pingan/policy_skills/disposition/SKILL.md",
        "backend/.deer-flow/pingan-context/software-path-catalog.sqlite",
    )
    database = checkout / "backend/.deer-flow/soc-validation/memory-dev-web/soc-memory-dev.sqlite"
    protected = [checkout / path for path in required]
    protected.extend((database, Path(f"{database}-wal"), Path(f"{database}-shm"), checkout / "config.yaml", checkout / "extensions_config.json"))
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"preserved fixture: {path.name}\n".encode())
    original = {path: path.read_bytes() for path in protected}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    host_python = checkout / "backend/.venv/bin/python"
    host_python.parent.mkdir(parents=True)
    for path in [*(bin_dir / name for name in ("docker", "curl", "uv")), host_python]:
        path.write_text(f"#!{sys.executable}\n{_EXECUTABLE}", encoding="utf-8", newline="\n")
        path.chmod(0o755)
    trace_path = tmp_path / "trace.jsonl"
    image_state = tmp_path / "base-image"
    env = {key: value for key, value in os.environ.items() if not key.startswith(("SOC_", "DEER_FLOW_", "BASH_FUNC_")) and key not in {"BASH_ENV", "ENV"}}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
        SOC_TEST_TRACE=str(trace_path),
        SOC_TEST_BASE_IMAGE=str(image_state),
        SOC_TEST_BACKEND=str(checkout / "backend"),
    )

    def run(command: str, *, base_exists: bool = False, migration_exit: int = 0, sync_exit: int = 0, sqlite_exit: int = 0, demo: str | None = None):
        if base_exists:
            image_state.touch()
        env["SOC_TEST_MIGRATION_EXIT"] = str(migration_exit)
        env["SOC_TEST_SYNC_EXIT"] = str(sync_exit)
        env["SOC_TEST_SQLITE_EXIT"] = str(sqlite_exit)
        if demo is not None:
            env["SOC_DEMO_AUTH_DISABLED"] = demo
        result = subprocess.run(
            [require_script_bash(), str(checkout / "scripts/soc-memory-dev.sh"), command],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        for path, content in original.items():
            assert path.is_file() and path.read_bytes() == content, f"Startup changed existing data: {path}"
        return result, records

    return run


def _calls(records: list[dict], tool: str, command: str) -> list[dict]:
    return [record for record in records if record["tool"] == tool and command in record["args"]]


@pytest.mark.parametrize("command,base_exists", [("start", False), ("start", True), ("rebuild", True)])
def test_soc_image_build_and_container_migration_precede_start(startup, command: str, base_exists: bool) -> None:
    result, records = startup(command, base_exists=base_exists)
    assert result.returncode == 0, result.stdout + result.stderr
    base_builds = [record for record in _calls(records, "docker", "build") if record["args"][0] == "compose"]
    assert bool(base_builds) == (command == "rebuild" or not base_exists)
    (derived,) = [record for record in _calls(records, "docker", "build") if record["args"][0] == "build"]
    assert SOC_IMAGE in derived["args"]
    if base_builds:
        assert records.index(base_builds[-1]) < records.index(derived)
    (migration,) = [record for record in _calls(records, "docker", "run") if "gateway" in record["args"]]
    (up,) = _calls(records, "docker", "up")
    assert records.index(derived) < records.index(migration) < records.index(up)
    for record in (migration, up):
        assert any(value.endswith("docker-compose.soc-memory-dev.yaml") for value in record["args"])
    assert "--rm" in migration["args"] and "--no-deps" in migration["args"]
    (sync,) = _calls(records, "uv", "sync")
    (upgrade,) = _calls(records, "uv", "upgrade")
    assert "--locked" in sync["args"]
    assert "pingan-dev" in sync["args"]
    assert "--no-sync" in upgrade["args"]
    assert upgrade["database"] == "sqlite:////app/backend/.deer-flow/soc-validation/memory-dev-web/soc-memory-dev.sqlite"
    assert upgrade["args"][upgrade["args"].index("--database-url") + 1] == upgrade["database"]
    assert records.index(migration) < records.index(sync) < records.index(upgrade) < records.index(up)
    assert not _calls(records, "python", "upgrade")
    assert all(record["external_actions"] == "false" for record in (migration, up))
    assert all(record["demo"] == "0" for record in (migration, up))


@pytest.mark.parametrize("command", ["start", "rebuild", "demo-start"])
def test_migration_failure_preserves_data_and_prevents_service_start(startup, command: str) -> None:
    result, records = startup(command, base_exists=True, migration_exit=37)
    assert result.returncode == 37, result.stdout + result.stderr
    assert len(_calls(records, "uv", "upgrade")) == 1
    assert not _calls(records, "docker", "up")
    assert not _calls(records, "docker", "down")


@pytest.mark.parametrize("failure", [{"sync_exit": 38}, {"sqlite_exit": 38}])
def test_unready_container_runtime_prevents_migration_and_start(startup, failure: dict[str, int]) -> None:
    result, records = startup("start", base_exists=True, **failure)
    assert result.returncode == 38, result.stdout + result.stderr
    assert len(_calls(records, "uv", "sync")) == 1
    assert not _calls(records, "uv", "upgrade")
    assert not _calls(records, "docker", "up")


@pytest.mark.parametrize("command,demo", [("demo-start", None), ("rebuild", "1")])
def test_existing_explicit_demo_mode_survives_container_migration(startup, command: str, demo: str | None) -> None:
    result, records = startup(command, base_exists=True, demo=demo)
    assert result.returncode == 0, result.stdout + result.stderr
    (migration,) = [record for record in _calls(records, "docker", "run") if "gateway" in record["args"]]
    (up,) = _calls(records, "docker", "up")
    assert all(record["demo"] == "1" for record in (migration, up))
    assert all(record["external_actions"] == "false" for record in (migration, up))


def test_soc_overlay_uses_distinct_image_and_keeps_persistent_database() -> None:
    services = yaml.safe_load((REPO_ROOT / "docker/docker-compose.soc-memory-dev.yaml").read_text(encoding="utf-8"))["services"]
    assert services["gateway"]["image"] == SOC_IMAGE
    environment = services["gateway"]["environment"]
    assert environment["SOC_DATABASE_URL"] == "sqlite:////app/backend/.deer-flow/soc-validation/memory-dev-web/soc-memory-dev.sqlite"
    assert environment["SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS"] == "false"
    for name in ("gateway", "frontend"):
        assert services[name]["environment"]["DEER_FLOW_AUTH_DISABLED"] == "${SOC_DEMO_AUTH_DISABLED:-0}"
