from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "backend/scripts/soc_pingan_zeus_lifecycle_response_probe.py"
    spec = importlib.util.spec_from_file_location(
        "soc_pingan_zeus_lifecycle_response_probe_cli",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _InternalPort:
    mocked = False

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[str] = []

    def query(self, *, alert_id: str) -> dict:
        self.calls.append(alert_id)
        return self.response


def _request_file(tmp_path: Path) -> Path:
    path = tmp_path / "task-request.local.json"
    path.write_text(
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
    os.chmod(path, 0o600)
    return path


def test_response_probe_preserves_complete_business_error_response() -> None:
    module = _load_script_module()
    response = {
        "code": 65505,
        "message": "完整业务错误",
        "data": {"status": None, "nested": {"details": ["a", "b"]}},
    }
    port = _InternalPort(response)

    result = module.query_complete_lifecycle_response("2567610", port=port)

    assert result == response
    assert port.calls == ["2567610"]


def test_response_probe_cli_prints_and_writes_private_full_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()
    request_path = _request_file(tmp_path)
    output_path = tmp_path / "lifecycle-response.local.json"
    response = {
        "code": 65505,
        "message": "服务端原始消息",
        "data": {"reason": "diagnostic detail"},
    }
    monkeypatch.setattr(module, "_build_internal_port", lambda _environ: _InternalPort(response))

    exit_code = module.main(
        [
            "--confirm-live",
            "--request-file",
            str(request_path),
            "--output-path",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == response
    assert json.loads(output_path.read_text(encoding="utf-8")) == response
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert str(output_path) in captured.err


def test_response_probe_rejects_mock_provider() -> None:
    module = _load_script_module()

    class _MockPort(_InternalPort):
        mocked = True

    with pytest.raises(ValueError, match="internal Provider"):
        module.query_complete_lifecycle_response(
            "2567610",
            port=_MockPort({"code": 200}),
        )


def test_response_probe_rejects_permissive_request(tmp_path: Path) -> None:
    module = _load_script_module()
    request_path = _request_file(tmp_path)
    os.chmod(request_path, 0o644)

    with pytest.raises(ValueError, match="0600"):
        module._read_private_request(request_path)
