"""Build one read-only Mac command containing hashes and redacted reference shapes."""

import base64
import json
from pathlib import Path
import textwrap
import zlib

from build_command import baseline_sources
from diagnose_sources import yaml_reference

HERE = Path(__file__).resolve().parent


def make_reference():
    pins = json.loads((HERE / "source-pins.json").read_text(encoding="utf-8"))["files"]
    baseline = baseline_sources()
    return {
        "pins": pins,
        "yaml": {
            name: yaml_reference(data)
            for name, data in baseline.items()
            if name.endswith(".yaml")
        },
    }


def build():
    reference = make_reference()
    encoded = base64.b64encode(
        zlib.compress(json.dumps(reference, sort_keys=True).encode("utf-8"), 9)
    ).decode("ascii")
    source = (HERE / "diagnose_sources.py").read_text(encoding="utf-8")
    command = (
        """bash <<'SOC_PUB_QUEUE_DIAG'
set -euo pipefail
queue_project="${SEC_KNOW_PROJECT_ROOT:-$HOME/sec_know_model}"
queue_python="$queue_project/.venv/bin/python"
if [ ! -x "$queue_python" ]; then queue_python=python3; fi
echo '只读核对全部队列补丁文件；差异输出隐藏地址、凭据及未知字符串，不修改源码或启动服务。'
"$queue_python" -B - --project-root "$queue_project" <<'SOC_PUB_QUEUE_DIAG_PY'
"""
        + source
        + '\nimport base64, zlib\n_DIAGNOSTIC_REFERENCE = json.loads(zlib.decompress(base64.b64decode("""'
        + "\n".join(textwrap.wrap(encoded, 100))
        + '\n""")))\nraise SystemExit(main(_DIAGNOSTIC_REFERENCE))\nSOC_PUB_QUEUE_DIAG_PY\nSOC_PUB_QUEUE_DIAG\n'
    )
    (HERE / "DIAGNOSE-SOURCE-DIFFERENCES.sh").write_text(command, encoding="utf-8")
    print(
        json.dumps(
            {
                "files_checked_by_command": len(reference["pins"]),
                "private_source_embedded": False,
                "read_only": True,
            }
        )
    )


if __name__ == "__main__":
    build()
