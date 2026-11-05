"""Package one offline Mac command for preparing the legacy production release."""

import base64
import json
import lzma
import textwrap
from pathlib import Path
import importlib.util

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREV = load(
    "production_prepare_previous",
    HERE.parent / "step10f_topic_receipt_array/build_command.py",
)
BASE, COMPACT, MODULE, TEST = PREV.BASE, PREV.COMPACT, PREV.MODULE, PREV.TEST


def baseline():
    result = PREV.baseline()
    result.update(
        {
            name: entry["content"].encode("utf-8")
            for name, entry in PREV.contents()["files"].items()
        }
    )
    return result


def contents():
    before = baseline()
    files = {}
    for path in sorted((HERE / "payload").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = path.relative_to(HERE / "payload").as_posix()
        if name not in {
            MODULE + "topic_settings.py",
            TEST + "test_topic_prd_settings.py",
        }:
            raise ValueError("unexpected_production_prepare_payload")
        raw = path.read_bytes()
        compile(raw, name, "exec")
        files[name] = {
            "old_sha256": BASE.sha(before[name]) if name in before else None,
            "new_sha256": BASE.sha(raw),
            "content": raw.decode("utf-8"),
        }
    if len(files) != 2:
        raise ValueError("production_prepare_payload_missing")
    packet = PREV.contents()
    packet.update(
        files=files,
        source_baseline="step10f_topic_receipt_array_installed_sources",
        dependencies={
            name: BASE.sha(raw)
            for name, raw in before.items()
            if name not in files and not name.startswith(TEST)
        },
        test_files={
            name: raw.decode("utf-8")
            for name, raw in before.items()
            if name not in files and name.startswith(TEST)
        },
        tests=sorted(set(packet["tests"]) | {TEST + "test_topic_prd_settings.py"}),
    )
    return packet


def make_bundle():
    bundle = COMPACT.make_bundle(contents(), baseline(), PREV.installer_source())
    bundle["prepare"] = (HERE / "prepare.py").read_text(encoding="utf-8")
    return bundle


def build():
    raw = json.dumps(make_bundle(), ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    encoded = "\n".join(
        textwrap.wrap(base64.b64encode(lzma.compress(raw)).decode("ascii"), 100)
    )
    command = (
        "bash <<'SOC_PREPARE_TOPIC_PRD'\nset -euo pipefail\numask 077\n"
        'cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"\n'
        ".venv/bin/python -B - <<'PY_PREPARE_TOPIC_PRD'\n"
        "import base64,hashlib,json,lzma,os,types\nfrom pathlib import Path\n"
        "d=lzma.LZMADecompressor(memlimit=67108864)\n"
        'raw=d.decompress(base64.b64decode("""'
        + encoded
        + f'"""),max_length={len(raw) + 1})\n'
        f'if len(raw)!={len(raw)} or not d.eof or d.unused_data or hashlib.sha256(raw).hexdigest()!="{BASE.sha(raw)}":\n'
        '    raise SystemExit("发布准备脚本不完整，未修改项目。")\n'
        'b=json.loads(raw);i=types.ModuleType("release_installer");exec(compile(b["installer"],"<installer>","exec"),i.__dict__)\n'
        'n={};exec(compile(b["reconstructor"],"<reconstruct>","exec"),n)\n'
        'p={};exec(compile(b["prepare"],"<release-prepare>","exec"),p)\n'
        'print("准备生产发布：复用现有规则清单，校验源码并执行离线回归，通过后备份更新；不调用模型或ZEUS，不启动服务。",flush=True)\n'
        "try:\n"
        '    result=p["prepare"](Path.cwd(),Path(os.environ.get("SEC_RULE_REFINE_DATA_ROOT",str(Path.home()/"sec-rule-refine-data"))),os.environ,b,n["reconstruct"],i)\n'
        "except Exception as e:\n"
        '    result={"check":"rule_refine_production_release_prepare","status":"failed","error_type":type(e).__name__,"services_started":False,"model_calls":0,"redis_calls":0,"business_database_calls":0,"zeus_posts":0}\n'
        "    if isinstance(e,i.InstallError): result.update(error_code=str(e),file=e.relative_file)\n"
        "    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(1)\n"
        "print(json.dumps(result,ensure_ascii=False,indent=2))\n"
        'print("准备完成。请将当前完整项目按原流程打包发布；不用另进生产容器执行启用命令。")\n'
        "PY_PREPARE_TOPIC_PRD\nSOC_PREPARE_TOPIC_PRD\n"
    )
    target = HERE / "PREPARE-PRODUCTION.sh"
    target.write_text(command, encoding="utf-8")
    manifest = {
        "source_baseline": "step10f_topic_receipt_array_installed_sources",
        "command_sha256": BASE.sha(command.encode()),
        "runtime_files": {
            n: {k: e[k] for k in ("old_sha256", "new_sha256")}
            for n, e in contents()["files"].items()
        },
        "dynamic_release_files": [
            "run.sh",
            "config/rule_refine.topic.prd.json",
            "config/rule_refine.rules.prd.snapshot",
        ],
        "services_started": False,
        "model_calls": 0,
        "zeus_posts": 0,
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    print(build())
