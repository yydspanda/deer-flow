"""Build a complete check without depending on the operator's old diagnostics."""

import base64
import hashlib
import json
import os
import tempfile
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step06j_synthetic_chain"
DIAGNOSTIC = HERE.parent / "step06k_output_diagnostic"
OLD = "1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117"
NEW = "c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0"
BASIC_PINS = {
    "runner.py": "5560912497769e7496abc9d6698ad8c3e12562633cf4215b518fee910780f062",
    "probe.py": "508eb4db65c072fdf6de2ecc113e3d62fa2624a5c9adb1ad9be3b96b8d147c15",
    "pins.json": "75a50783a9dae5016e0cae26f9380f73cf54480492c847169175aeb0eda4087c",
}


def prepare(files):
    """Pure transformations of pinned prior tools, never the application files."""
    if set(files) != set(BASIC_PINS):
        raise ValueError("基础工具清单不匹配")
    result = dict(files)
    runner = result["runner.py"].decode("utf-8")
    changes = [
        ('                "background_service",', '                "background_service",\n                "background_tick",\n                "task_source",'),
        ('预检通过：3条人工告警 + 1次重发；隔离Redis计数；请求模型=', '预检通过：临时SQLite + 隔离Redis + 后台处理函数；3条人工告警、1次重发；模型='),
        ('"counter_mode": "demo",', '"counter_mode": "live",\n                "counter_namespace_scope": "isolated_synthetic",\n                "database_scope": "temporary_sqlite",\n                "background_tick_invocation": "manual_in_process",'),
        ('"rule_refine_synthetic_chain"', '"rule_refine_background_materials"'),
        ('开始隔离串联；模型请求可能需要几分钟，请等待。', '开始后台函数隔离验证；最多一次SDK请求，请等待。'),
    ]
    for before, after in changes:
        if runner.count(before) != 1:
            raise ValueError("旧验证工具位置不匹配")
        runner = runner.replace(before, after, 1)
    result["runner.py"] = runner.encode("utf-8")
    result["base_probe.py"] = result.pop("probe.py")
    for name in ("pins.json",):
        text = result[name].decode("utf-8")
        if text.count(OLD) != 1:
            raise ValueError("旧解析器摘要不匹配")
        result[name] = text.replace(OLD, NEW, 1).encode("utf-8")
    return result


def unpack(payload, expected):
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode("".join(payload.split()), validate=True), 256 * 1024 + 1)
    if len(raw) > 256 * 1024 or not decoder.eof or decoder.unused_data or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("命令内容不完整或校验失败；未连接服务")
    files = json.loads(raw)
    if not isinstance(files, dict) or set(files) != {"probe.py", "diagnostics.py", "entry.py"}:
        raise ValueError("新工具清单不匹配")
    result = {}
    for name, content in files.items():
        if not isinstance(content, str):
            raise ValueError("新工具内容无效")
        compile(content, name, "exec")
        result[name] = content.encode("utf-8")
    return result


def publish(source, bundled):
    files = {}
    for name, expected in BASIC_PINS.items():
        raw = (source / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("原基础工具版本不符：" + name)
        files[name] = raw
    files = {**prepare(files), **bundled}
    for name, raw in files.items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    folder = Path(tempfile.mkdtemp(prefix="background-materials-check-", dir=source.parent))
    for name, raw in files.items():
        fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    return folder


def main():
    source = {name: (PREVIOUS / name).read_bytes() for name in ("runner.py", "probe.py", "pins.json")}
    transformed = prepare(source)
    diagnostic = (DIAGNOSTIC / "diagnostics.py").read_bytes()
    if diagnostic.count(OLD.encode()) != 1:
        raise ValueError("诊断源版本不匹配")
    transformed["diagnostics.py"] = diagnostic.replace(OLD.encode(), NEW.encode(), 1)
    transformed["entry.py"] = (DIAGNOSTIC / "entry.py").read_bytes()
    for name, raw in transformed.items():
        (HERE / name).write_bytes(raw)
    content = json.dumps({name: (HERE / name).read_text(encoding="utf-8") for name in ("probe.py", "diagnostics.py", "entry.py")}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = "\n".join(textwrap.wrap(base64.b64encode(zlib.compress(content, 9)).decode(), 100))
    expected = hashlib.sha256(content).hexdigest()
    unpack(payload, expected)
    # Carry the reviewed new helpers rather than import unknown legacy diagnostics.
    transformer = Path(__file__).read_text(encoding="utf-8")
    transformer = transformer[transformer.index("OLD = "):transformer.index("\n\ndef main():")]
    command = '''bash <<'SOC_BACKGROUND_MATERIALS'
set -euo pipefail
umask 077
cd "$HOME/sec_know_model"
.venv/bin/python -B - <<'PY_CHECK'
import base64, hashlib, json, os, subprocess, sys, tempfile, zlib
from pathlib import Path

parent = Path.home() / "sec-rule-refine-data"
source = parent / "synthetic-chain-diagnostic-o4olbtkt"
'''
    command += transformer + '\npayload = """\n' + payload + '\n"""\n'
    command += f'expected_payload_sha256 = "{expected}"\n'
    command += '''try:
    folder = publish(source, unpack(payload, expected_payload_sha256))
except Exception as error:
    report = {"status": "preflight_failed", "error_type": type(error).__name__, "model_calls": 0}
    if isinstance(error, ValueError) and str(error).startswith(("原基础工具版本不符：", "基础工具清单", "旧验证工具位置", "旧解析器摘要", "命令内容", "新工具")):
        report["reason"] = str(error)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1)
print("本次验证目录：" + str(folder), flush=True)
raise SystemExit(subprocess.call([sys.executable, "-B", str(folder / "entry.py")]))
PY_CHECK
SOC_BACKGROUND_MATERIALS
'''
    (HERE / "CHECK-BACKGROUND-MATERIALS.sh").write_text(command, encoding="utf-8")
    print("Built CHECK-BACKGROUND-MATERIALS.sh")


if __name__ == "__main__":
    main()
