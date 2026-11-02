"""Build a self-contained command; only source reads and private file writes."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPLEMENTATION = HERE.parent
PURE_ROOT = IMPLEMENTATION / "step03d_preparation/payload/my_workflows/zeus/rule_refine"
PURE_FILES = ("rule_config_source.py", "csv_config_source.py", "ingress.py")


def sources():
    files = {
        name: (PURE_ROOT / name).read_text(encoding="utf-8") for name in PURE_FILES
    }
    files["materials_check.py"] = (
        IMPLEMENTATION / "step06o_ingress_readiness/materials_check.py"
    ).read_text(encoding="utf-8")
    for name in ("request_schema.py", "prepare.py"):
        files[name] = (HERE / name).read_text(encoding="utf-8")
    return files


def build():
    files = sources()
    for name, source in files.items():
        compile(source, name, "exec")
    packet = {
        "files": files,
        "source_pins": json.loads(
            (HERE / "source-pins.json").read_text(encoding="utf-8")
        ),
        "pure_pins": {
            name: hashlib.sha256((PURE_ROOT / name).read_bytes()).hexdigest()
            for name in PURE_FILES
        },
    }
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    digest = hashlib.sha256(raw).hexdigest()
    runner = (
        (HERE / "runner.py")
        .read_text(encoding="utf-8")
        .replace("__PAYLOAD__", payload)
        .replace("__DIGEST__", digest)
    )
    compile(runner, "embedded_runner", "exec")
    command = (
        "bash <<'SOC_PREPARE_ONE_ALERT'\nset -euo pipefail\ncd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_PREPARE_ONE'\n"
        + runner
        + "\nPY_PREPARE_ONE\nSOC_PREPARE_ONE_ALERT\n"
    )
    (HERE / "PREPARE-ONE-ALERT.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
