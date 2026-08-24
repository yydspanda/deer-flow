from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_NGINX_CONFIG = REPO_ROOT / "docker" / "nginx" / "nginx.local.conf"


def test_local_nginx_runtime_files_stay_under_prefix() -> None:
    content = LOCAL_NGINX_CONFIG.read_text(encoding="utf-8")

    assert "error_log logs/nginx-error.log warn;" in content
    assert "pid logs/nginx.pid;" in content
    assert "lock_file logs/nginx.lock;" in content
    for directive, path in (
        ("client_body_temp_path", "temp/client_body_temp"),
        ("proxy_temp_path", "temp/proxy_temp"),
        ("fastcgi_temp_path", "temp/fastcgi_temp"),
        ("uwsgi_temp_path", "temp/uwsgi_temp"),
        ("scgi_temp_path", "temp/scgi_temp"),
    ):
        assert f"{directive} {path};" in content
