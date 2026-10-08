"""File detection correction must leave unrelated HIDS projections unchanged."""

import json

from soc_agent.normalizers import normalize_alert_payload


def test_non_webshell_login_retains_session_user_and_legacy_file_projection():
    fields = {"event_type": "login", "login_user": "alice", "process__file__path": "/usr/sbin/sshd"}
    message = " ".join(f"{key}={json.dumps(value)}" for key, value in fields.items())
    alert = normalize_alert_payload({"alert": {"alertId": "synthetic-login", "hitLog": [{"topic": "security_qthids", "zeusRawLogs": [{"message": message}]}]}})
    assert alert.entities.user.username == "alice"
    assert alert.entities.file.file_path == "/usr/sbin/sshd"
