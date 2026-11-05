"""Build a source-pinned installer containing algorithms/tests, never private source."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path
import textwrap
import zipfile
import zlib

from install import PATHS, transform
import scoped_proxy
import scoped_python

HERE = Path(__file__).resolve().parent
IMPLEMENTATION = HERE.parent
ZIP = HERE.parents[2] / "sec_know_model.zip"
BUNDLE_FILES = (
    "install.py",
    "routing_transforms.py",
    "launcher_transforms.py",
    "proxy_transforms.py",
    "test_routing.py",
    "test_launchers.py",
    "test_proxy.py",
    "source-pins.json",
    "source-contracts.json",
    "scoped_proxy.py",
    "scoped_python.py",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def baseline_sources():
    additions = {
        "my_workflows/zeus/rule_refine/background_service.py": "step07_surge_runtime",
        "test/rule_refine/test_background_service.py": "step06w_unified_limits",
    }
    sources = {}
    with zipfile.ZipFile(ZIP) as archive:
        for path in PATHS:
            sources[path] = (
                (IMPLEMENTATION / additions[path] / "payload" / path).read_bytes()
                if path in additions
                else archive.read(path)
            )
    return sources


def variants():
    sources = baseline_sources()
    result = {path: [data] for path, data in sources.items()}
    manifest = json.loads(
        (IMPLEMENTATION / "step06f_background/install-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for path, entry in manifest["updates"].items():
        assert digest(sources[path]) == entry["old"]
        result[path].append(sources[path] + entry["append"].encode("utf-8"))
        assert digest(result[path][-1]) == entry["new"]
    tree = ast.parse(
        (IMPLEMENTATION / "step07g_release_disable/patch.py").read_text(
            encoding="utf-8"
        )
    )
    block = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "BLOCK" for t in node.targets)
    )
    first, rest = sources["run.sh"].split(b"\n", 1)
    result["run.sh"].append(first + b"\n" + block + rest)
    controller = "my_workflows/zeus/flows/alert_controller.py"
    confirmed = sources[controller].replace(b'model_name = "PingAnGPT_R"', b'model_name = "Qwen38_27B"', 1)
    # Reconstructed from the user-provided one-line change, not accepted by hash alone.
    assert digest(confirmed) == "d24a93c167428804d1ac606f3a034b168592e3fd1e692d6cf1e26b1104329022"
    result[controller].append(confirmed)
    return result


def build():
    baseline = baseline_sources()
    scoped_paths = {
        "llm_service/litellm_proxy_config.prd.yaml": scoped_proxy,
        "llm_service/openai_completion.py": scoped_proxy,
        "my_workflows/zeus/flows/alert_controller.py": scoped_python,
        "test/test_all_models.py": scoped_python,
    }
    contracts = {
        name: module.build_contract(name, baseline[name])
        for name, module in scoped_paths.items()
    }
    (HERE / "source-contracts.json").write_text(
        json.dumps(contracts, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    pins = {
        path: {digest(data): digest(transform(path, data)) for data in choices}
        for path, choices in variants().items()
    }
    manifest = {
        "schema": "sec_know.pub_queue_patch.v1",
        "reviewed_zip_sha256": digest(ZIP.read_bytes()),
        "files": pins,
    }
    (HERE / "source-pins.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    payload = json.dumps(
        {
            name: base64.b64encode((HERE / name).read_bytes()).decode("ascii")
            for name in BUNDLE_FILES
        },
        sort_keys=True,
    ).encode("utf-8")
    compressed = zlib.compress(payload, level=9)
    encoded = "\n".join(
        textwrap.wrap(base64.b64encode(compressed).decode("ascii"), 100)
    )
    bootstrap = '''import base64, hashlib, json, os, pathlib, subprocess, sys, tempfile, zlib
blob = base64.b64decode("""PAYLOAD""")
if hashlib.sha256(blob).hexdigest() != "DIGEST":
    raise SystemExit("queue patch bundle checksum mismatch")
files = json.loads(zlib.decompress(blob))
with tempfile.TemporaryDirectory(prefix="sec-know-pub-queue-") as directory:
    root = pathlib.Path(directory)
    for name, value in files.items():
        if pathlib.Path(name).name != name:
            raise SystemExit("invalid bundle path")
        path = root / name
        path.write_bytes(base64.b64decode(value))
        path.chmod(0o600)
    result = subprocess.run([sys.executable, "-B", str(root / "install.py"), "--project-root", sys.argv[1], "--backup-parent", sys.argv[2]])
    raise SystemExit(result.returncode)
'''.replace("PAYLOAD", encoded).replace("DIGEST", digest(compressed))
    command = (
        """bash <<'SOC_PUB_QUEUE_27'
set -euo pipefail
umask 077
queue_project="${SEC_KNOW_PROJECT_ROOT:-$HOME/sec_know_model}"
queue_backup_parent="${SEC_KNOW_BACKUP_PARENT:-$HOME/sec-rule-refine-data}"
queue_python="$queue_project/.venv/bin/python"
if [ ! -x "$queue_python" ]; then queue_python=python3; fi
echo '合并告警队列：PRD/LOCAL用Pub，生产并发27；STG用Qwen38_27B并复用原PingAnGPT_R队列。先执行离线回归，备份后修改源码，不启动服务。'
"$queue_python" -B - "$queue_project" "$queue_backup_parent" <<'SOC_PUB_QUEUE_PYTHON'
"""
        + bootstrap
        + "SOC_PUB_QUEUE_PYTHON\nSOC_PUB_QUEUE_27\n"
    )
    (HERE / "INSTALL-PUB-QUEUE-27.sh").write_text(command, encoding="utf-8")
    print(
        json.dumps(
            {
                "files_pinned": len(pins),
                "bundle_files": len(BUNDLE_FILES),
                "private_sources_embedded": False,
                "model_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    raise SystemExit(
        "This queue installer is retired; do not rebuild it. See README.md."
    )
