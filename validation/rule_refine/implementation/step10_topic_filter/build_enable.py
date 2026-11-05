"""Produce the explicit offline enable command without bundling private settings."""

import base64
import hashlib
import json
import lzma
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent


def make_command(expected, installer_source):
    source = (HERE / "enable_topic.py").read_text(encoding="utf-8")
    compile(source, "<topic-enable>", "exec")
    raw = json.dumps(
        {"expected": expected, "installer": installer_source, "source": source},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    encoded = "\n".join(
        textwrap.wrap(base64.b64encode(lzma.compress(raw)).decode("ascii"), 100)
    )
    return (
        "bash <<'SOC_ENABLE_TOPIC'\nset -euo pipefail\numask 077\n"
        'cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"\n'
        "if [ -x .venv/bin/python ]; then SEC_TOPIC_PYTHON=.venv/bin/python; else SEC_TOPIC_PYTHON=python3; fi\n"
        "\"$SEC_TOPIC_PYTHON\" -B - <<'PY_ENABLE_TOPIC'\n"
        "import base64,hashlib,json,lzma,os,types\nfrom pathlib import Path\n"
        "d=lzma.LZMADecompressor(memlimit=67108864)\n"
        'raw=d.decompress(base64.b64decode("""'
        + encoded
        + f'"""),max_length={len(raw) + 1})\n'
        f'if len(raw)!={len(raw)} or not d.eof or d.unused_data or hashlib.sha256(raw).hexdigest()!="{digest}":\n'
        '    raise SystemExit("启用命令载荷不完整，未修改项目。")\n'
        'b=json.loads(raw);i=types.ModuleType("topic_enable_installer");exec(compile(b["installer"],"<installer>","exec"),i.__dict__)\n'
        'n={};exec(compile(b["source"],"<topic-enable>","exec"),n)\n'
        'print("配置topic推荐与ZEUS提交；备份后修改启用配置，生产仅调整run.sh的监测标记；不启动服务、不调用模型或接口。",flush=True)\n'
        "try:\n"
        '    result=n["configure"](Path.cwd(),Path(os.environ.get("SEC_RULE_REFINE_DATA_ROOT",str(Path.home()/"sec-rule-refine-data"))),os.environ,b["expected"],i)\n'
        "except Exception as e:\n"
        '    result={"check":"rule_refine_topic_enable","status":"failed","error_type":type(e).__name__,"services_started":False,"model_calls":0,"zeus_callback_calls":0}\n'
        "    if isinstance(e,i.InstallError): result.update(error_code=str(e),file=e.relative_file)\n"
        "    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(1)\n"
        "print(json.dumps(result,ensure_ascii=False,indent=2))\n"
        "PY_ENABLE_TOPIC\nSOC_ENABLE_TOPIC\n"
    )
