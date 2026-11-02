import base64
import hashlib
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build():
    raw = (HERE / "submit.py").read_bytes()
    compile(raw, "submit.py", "exec")
    digest = hashlib.sha256(raw).hexdigest()
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode(), 100)
    )
    loader = '''import base64, hashlib, zlib
payload = """__PAYLOAD__"""
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode(payload), 256 * 1024 + 1)
if (len(raw) > 256 * 1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__DIGEST__"):
    raise SystemExit("验证工具校验失败；未提交。")
exec(compile(raw, "one_alert_ingress_check", "exec"), {"__name__": "__main__"})
'''.replace("__PAYLOAD__", payload).replace("__DIGEST__", digest)
    command = (
        "bash <<'SOC_SUBMIT_ONE_ALERT'\nset -euo pipefail\ncd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_ONE_ALERT'\n"
        + loader
        + "PY_ONE_ALERT\nSOC_SUBMIT_ONE_ALERT\n"
    )
    (HERE / "SUBMIT-ONE-ALERT.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
