"""Fix Mac temporary-path aliases in the frozen production preparation command."""

import base64
import importlib.util
import json
import lzma
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step10g_production_prepare"
REVISION = "macos-canonical-temp-path-v1"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREV = load("macos_prepare_original_builder", PREVIOUS / "build_command.py")
BASE = PREV.BASE
baseline, contents = PREV.baseline, PREV.contents


def make_bundle():
    bundle = PREV.make_bundle()
    before = "            stage = Path(directory)\n"
    if bundle["prepare"].count(before) != 1:
        raise ValueError("production_prepare_template_changed")
    bundle["prepare"] = bundle["prepare"].replace(
        before,
        "            # macOS /var and /private/var can denote the same directory.\n"
        "            stage = Path(directory).resolve()\n",
    )
    compile(bundle["prepare"], "<macos-release-prepare>", "exec")
    return bundle


def build():
    bundle = make_bundle()
    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode()
    encoded = "\n".join(
        textwrap.wrap(base64.b64encode(lzma.compress(raw)).decode("ascii"), 100)
    )
    command = (
        "bash <<'SOC_PREPARE_TOPIC_PRD_MAC'\nset -euo pipefail\numask 077\n"
        'cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"\n'
        ".venv/bin/python -B - <<'PY_PREPARE_TOPIC_PRD_MAC'\n"
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
        'print("准备生产发布（Mac路径修正版）：复用规则清单，先校验并执行离线回归，通过后备份更新；不调用模型或ZEUS，不启动服务。",flush=True)\n'
        "try:\n"
        '    result=p["prepare"](Path.cwd(),Path(os.environ.get("SEC_RULE_REFINE_DATA_ROOT",str(Path.home()/"sec-rule-refine-data"))),os.environ,b,n["reconstruct"],i)\n'
        "except Exception as e:\n"
        f'    result={{"check":"rule_refine_production_release_prepare","preparation_revision":"{REVISION}","status":"failed","error_type":type(e).__name__,"services_started":False,"model_calls":0,"redis_calls":0,"business_database_calls":0,"zeus_posts":0}}\n'
        "    if isinstance(e,i.InstallError): result.update(error_code=str(e),file=e.relative_file)\n"
        "    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(1)\n"
        f'result["preparation_revision"]="{REVISION}"\n'
        "print(json.dumps(result,ensure_ascii=False,indent=2))\n"
        'print("准备完成。请将当前完整项目按原流程打包发布；不用另进生产容器执行启用命令。")\n'
        "PY_PREPARE_TOPIC_PRD_MAC\nSOC_PREPARE_TOPIC_PRD_MAC\n"
    )
    target = HERE / "PREPARE-PRODUCTION.sh"
    target.write_text(command, encoding="utf-8")
    manifest = {
        "preparation_revision": REVISION,
        "source_baseline": "step10f_topic_receipt_array_installed_sources",
        "command_sha256": BASE.sha(command.encode()),
        "original_command_sha256": BASE.sha(
            (PREVIOUS / "PREPARE-PRODUCTION.sh").read_bytes()
        ),
        "original_preparation_sha256": BASE.sha((PREVIOUS / "prepare.py").read_bytes()),
        "fixed_preparation_sha256": BASE.sha(bundle["prepare"].encode()),
        "runtime_payload_identical_to_step10g": True,
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
