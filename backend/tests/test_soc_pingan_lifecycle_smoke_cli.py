from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "backend/scripts/soc_pingan_zeus_lifecycle_smoke.py"
    spec = importlib.util.spec_from_file_location(
        "soc_pingan_zeus_lifecycle_smoke_cli",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lifecycle_smoke_cli_reads_private_prepared_request(tmp_path: Path) -> None:
    module = _load_script_module()
    request_path = tmp_path / "task-request.local.json"
    request_path.write_text(
        json.dumps(
            {
                "app_code": "zeus",
                "flow_id": "alert_agent",
                "session_id": "session-1",
                "alert_id": "2567610",
                "alert_data": {"alert": {"id": "2567610"}},
            }
        ),
        encoding="utf-8",
    )
    os.chmod(request_path, 0o600)

    request = module._read_private_request(request_path)

    assert request.alert_id == "2567610"


def test_lifecycle_smoke_cli_rejects_permissive_request(tmp_path: Path) -> None:
    module = _load_script_module()
    request_path = tmp_path / "task-request.local.json"
    request_path.write_text("{}", encoding="utf-8")
    os.chmod(request_path, 0o644)

    with pytest.raises(ValueError, match="0600"):
        module._read_private_request(request_path)
