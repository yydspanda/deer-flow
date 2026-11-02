"""Build a readable command reusing the operator's completed 6J tools."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
header = """bash <<'SOC_OUTPUT_CHECK'
set -euo pipefail
umask 077
cd "$HOME/sec_know_model"
soc_diag_work="$(.venv/bin/python - <<'PY_SETUP'
import hashlib, os, tempfile
from pathlib import Path

parent = Path.home() / "sec-rule-refine-data"
source = parent / "synthetic-chain-check-r46kqixu"
expected = {
    "runner.py": "5560912497769e7496abc9d6698ad8c3e12562633cf4215b518fee910780f062",
    "probe.py": "508eb4db65c072fdf6de2ecc113e3d62fa2624a5c9adb1ad9be3b96b8d147c15",
    "pins.json": "75a50783a9dae5016e0cae26f9380f73cf54480492c847169175aeb0eda4087c",
}
files = {}
for name, digest in expected.items():
    raw = (source / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SystemExit("停止：原验证工具版本不符：" + name)
    files[name] = raw
folder = Path(tempfile.mkdtemp(prefix="synthetic-chain-diagnostic-", dir=parent))
for name, raw in files.items():
    fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
print(folder)
PY_SETUP
)"
"""
command = header
for name, delimiter in (("diagnostics.py", "PY_DIAG"), ("entry.py", "PY_ENTRY")):
    command += f"cat > \"$soc_diag_work/{name}\" <<'{delimiter}'\n"
    command += (HERE / name).read_text(encoding="utf-8") + delimiter + "\n"
command += """printf '本次诊断目录：%s\\n' "$soc_diag_work"
.venv/bin/python -B "$soc_diag_work/entry.py"
SOC_OUTPUT_CHECK
"""
(HERE / "CHECK-OUTPUT.sh").write_text(command, encoding="utf-8")
print("Built CHECK-OUTPUT.sh")
