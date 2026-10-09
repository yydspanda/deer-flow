from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

WRAPPER = Path(__file__).with_name("soc_pingan_reset_dev.sh")
SCHEMA = "soc.pingan_dev_database_reset.v1"
APPLY_CALLS = [
    ["reset-dev-data"],
    ["stop"],
    ["reset-dev-data"],
    ["reset-dev-data", "--confirm", "RESET-SOC-DEV"],
]

FAKE_HOST = r"""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
config = json.loads((root / "fake-host.json").read_text(encoding="utf-8"))
call_file = root / "host-calls.jsonl"
previous = [json.loads(line) for line in call_file.read_text(encoding="utf-8").splitlines()] if call_file.exists() else []
args = sys.argv[1:]
with call_file.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
if args == ["stop"]:
    print("All services stopped")
    sys.exit(config.get("stop_exit", 0))
if args == ["reset-dev-data"]:
    preview_index = sum(call == ["reset-dev-data"] for call in previous)
    previews = config.get("previews", [{}])
    changes = previews[min(preview_index, len(previews) - 1)]
    payload = {
        "schema_version": "soc.pingan_dev_database_reset.v1",
        "status": "preview",
        "database": str(root / "backend/.deer-flow/data/soc_agent_dev.db"),
        "ready": True,
        "blockers": [],
        "files": [],
    }
    payload.update(changes)
    print(json.dumps(payload))
    sys.exit(config.get("preview_exit", 0))
if args == ["reset-dev-data", "--confirm", "RESET-SOC-DEV"]:
    if config.get("reset_exit", 0):
        print("reset failed without changing database", file=sys.stderr)
        sys.exit(config["reset_exit"])
    payload = {
        "schema_version": "soc.pingan_dev_database_reset.v1",
        "status": "reset",
        "database": str(root / "backend/.deer-flow/data/soc_agent_dev.db"),
        "ready": True,
        "blockers": [],
        "files": [],
        "backup_directory": str(root / "backend/.deer-flow/data/soc-dev-reset-backups/test-backup"),
    }
    payload.update(config.get("receipt", {}))
    print(json.dumps(payload))
    sys.exit(0)
print("unexpected fake Host invocation: " + repr(args), file=sys.stderr)
sys.exit(99)
"""


@dataclass
class FakeDeployment:
    root: Path
    home: Path

    def configure(self, **configuration: object) -> None:
        (self.root / "fake-host.json").write_text(
            json.dumps(configuration), encoding="utf-8"
        )

    def calls(self) -> list[list[str]]:
        path = self.root / "host-calls.jsonl"
        return (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )

    @property
    def receipt(self) -> Path:
        return self.root / "backend/.deer-flow/internal-host-dev/dev-reset-receipt.json"

    def history(self) -> list[Path]:
        return sorted((self.receipt.parent / "reset-receipts").glob("*.json"))

    def run(
        self, *args: str, explicit_root: bool = True
    ) -> subprocess.CompletedProcess:
        command = ["bash", str(WRAPPER)]
        if explicit_root:
            command.extend(["--root", str(self.root)])
        environment = dict(os.environ, HOME=str(self.home))
        return subprocess.run(
            [*command, *args],
            cwd=self.home,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )


@pytest.fixture
def deployment(tmp_path: Path) -> FakeDeployment:
    home = tmp_path / "operator home"
    root = home / "deer-flow"
    (root / "backend/.venv/bin").mkdir(parents=True)
    (root / "backend/.venv/bin/python").symlink_to(sys.executable)
    (root / "scripts").mkdir()
    (root / "scripts/soc_pingan_macos_host_dev.py").write_text(
        FAKE_HOST, encoding="utf-8"
    )
    instance = FakeDeployment(root, home)
    instance.configure()
    return instance


@pytest.mark.parametrize("explicit_root", [True, False])
def test_default_is_preview_without_stopping_or_resetting(
    deployment: FakeDeployment, explicit_root: bool
) -> None:
    result = deployment.run(explicit_root=explicit_root)

    assert result.returncode == 0, result.stderr
    assert deployment.calls() == [["reset-dev-data"]]
    assert not deployment.receipt.exists()
    assert deployment.history() == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--confirm", "RESET-SOC-STG"],
        ["--confirm"],
        ["--unknown"],
        ["RESET-SOC-DEV"],
    ],
)
def test_invalid_arguments_make_no_host_calls(
    deployment: FakeDeployment, arguments: list[str]
) -> None:
    result = deployment.run(*arguments)

    assert result.returncode != 0
    assert deployment.calls() == []


def test_help_makes_no_host_calls(deployment: FakeDeployment) -> None:
    result = deployment.run("--help")

    assert result.returncode == 0, result.stderr
    assert "RESET-SOC-DEV" in result.stdout
    assert deployment.calls() == []


@pytest.mark.parametrize(
    "configuration",
    [
        {"preview_exit": 1},
        {"previews": [{"database": "/somewhere/soc_agent_stg.db"}]},
        {"previews": [{"schema_version": "unknown.v9"}]},
        {"previews": [{"status": "reset"}]},
    ],
)
def test_invalid_initial_preview_never_stops_services(
    deployment: FakeDeployment, configuration: dict
) -> None:
    deployment.configure(**configuration)

    result = deployment.run("--confirm", "RESET-SOC-DEV")

    assert result.returncode != 0
    assert deployment.calls() == [["reset-dev-data"]]
    assert not deployment.receipt.exists()


def test_stop_failure_prevents_apply(deployment: FakeDeployment) -> None:
    deployment.configure(stop_exit=7)

    result = deployment.run("--confirm", "RESET-SOC-DEV")

    assert result.returncode != 0
    assert deployment.calls() == APPLY_CALLS[:2]
    assert not deployment.receipt.exists()


@pytest.mark.parametrize(
    "post_stop",
    [
        {"ready": False, "blockers": ["port 2026 is still listening"]},
        {"ready": True, "blockers": ["database is open"]},
        {"ready": False, "blockers": []},
        {"ready": "true", "blockers": []},
        {"database": "/somewhere/soc_agent_dev.db"},
    ],
)
def test_post_stop_recheck_blocks_reset(
    deployment: FakeDeployment, post_stop: dict
) -> None:
    deployment.configure(previews=[{}, post_stop])

    result = deployment.run("--confirm", "RESET-SOC-DEV")

    assert result.returncode != 0
    assert deployment.calls() == APPLY_CALLS[:3]
    assert not deployment.receipt.exists()


def test_reset_failure_preserves_prior_receipt(deployment: FakeDeployment) -> None:
    deployment.receipt.parent.mkdir(parents=True)
    previous = '{"previous": "verified reset receipt"}\n'
    deployment.receipt.write_text(previous, encoding="utf-8")
    deployment.configure(reset_exit=9)

    result = deployment.run("--confirm", "RESET-SOC-DEV")

    assert result.returncode != 0
    assert deployment.calls() == APPLY_CALLS
    assert deployment.receipt.read_text(encoding="utf-8") == previous
    assert deployment.history() == []


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "unknown.v9"},
        {"status": "preview"},
        {"database": "/somewhere/soc_agent_stg.db"},
    ],
)
def test_invalid_receipt_does_not_replace_previous_receipt(
    deployment: FakeDeployment, changes: dict
) -> None:
    deployment.receipt.parent.mkdir(parents=True)
    previous = '{"previous": "verified reset receipt"}\n'
    deployment.receipt.write_text(previous, encoding="utf-8")
    deployment.configure(receipt=changes)

    result = deployment.run("--confirm", "RESET-SOC-DEV")

    assert result.returncode != 0
    assert deployment.calls() == APPLY_CALLS
    assert deployment.receipt.read_text(encoding="utf-8") == previous
    assert deployment.history() == []


@pytest.mark.parametrize("status", ["reset", "already_empty"])
def test_apply_records_private_receipts_and_never_starts_services(
    deployment: FakeDeployment, status: str
) -> None:
    deployment.configure(
        previews=[{"ready": False, "blockers": ["services running"]}, {}],
        receipt={"status": status},
    )

    result = deployment.run("--confirm", "RESET-SOC-DEV")

    assert result.returncode == 0, result.stderr
    assert deployment.calls() == APPLY_CALLS
    receipt = json.loads(deployment.receipt.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == SCHEMA
    assert receipt["status"] == status
    assert receipt["database"] == str(
        deployment.root / "backend/.deer-flow/data/soc_agent_dev.db"
    )
    history = deployment.history()
    assert len(history) == 1
    assert json.loads(history[0].read_text(encoding="utf-8")) == receipt
    assert deployment.receipt.stat().st_mode & 0o777 == 0o600
    assert history[0].stat().st_mode & 0o777 == 0o600


def test_repeated_apply_keeps_each_reset_receipt(deployment: FakeDeployment) -> None:
    first = deployment.run("--confirm", "RESET-SOC-DEV")
    assert first.returncode == 0, first.stderr
    first_history = deployment.history()
    assert len(first_history) == 1
    first_contents = first_history[0].read_bytes()
    deployment.configure(receipt={"status": "already_empty"})

    second = deployment.run("--confirm", "RESET-SOC-DEV")

    assert second.returncode == 0, second.stderr
    assert deployment.calls() == APPLY_CALLS * 2
    assert len(deployment.history()) == 2
    assert first_history[0].read_bytes() == first_contents
    assert json.loads(deployment.receipt.read_text(encoding="utf-8"))["status"] == (
        "already_empty"
    )


def test_sourcing_wrapper_does_not_exit_parent_or_run_host(
    deployment: FakeDeployment,
) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "parent-shell-still-running\\n"',
            "test-parent",
            str(WRAPPER),
        ],
        cwd=deployment.home,
        env=dict(os.environ, HOME=str(deployment.home)),
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0
    assert "parent-shell-still-running" in result.stdout
    assert deployment.calls() == []
