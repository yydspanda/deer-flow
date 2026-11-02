bash <<'SOC_STEP04D'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYLIVE'
"""一次真实模型请求，输入为人工材料；仅保存独立验证文件。"""

import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

EXPECTED = {
    "my_workflows/zeus/rule_refine/contracts.py": "59986ced58a5c951d4fb2fdb1ffafee106a370da7e5e893dbee24a1db292356a",
    "my_workflows/zeus/rule_refine/rule_config_source.py": "dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941",
    "my_workflows/zeus/rule_refine/materials.py": "75b9b44e15737b6cf8a8b594ac077a1a9f5b8405d8797d068f1e3e5451dd88d9",
    "test/rule_refine/test_materials.py": "3045b6ddccdbc53260887eb5b2efa8726e1553cc196eb0b5f1595102659c0e76",
    "my_workflows/zeus/rule_refine/analysis.py": "1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117",
    "test/rule_refine/test_analysis.py": "a956151f1d3b2615cf745f1c879566af469fb18d8e8d1418c024fd2cf01d5f5f",
    "my_workflows/zeus/rule_refine/model_adapter.py": "be651bf6142293baf04c6f2c3c585ec0ea81ceeab1d20b1055f14dfe67f75790",
}
MODEL = "DeepSeek_V32"


class CheckError(ValueError):
    pass


def load_modules(root):
    for name, digest in EXPECTED.items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise CheckError("previous_module_version_mismatch:" + name)
    spec = importlib.util.spec_from_file_location("_live_check_fixtures", root / "test/rule_refine/test_analysis.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    adapter = importlib.import_module(fixtures.fixtures.package.__name__ + ".model_adapter")
    return fixtures, adapter


def save(directory, name, data):
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class CaptureClient:
    """只留正文诊断；不保存 SDK 原响应、请求头、推理字段或异常原文。"""

    def __init__(self, client):
        self.client = client
        self.base_url = client.base_url
        self.captured = {"received": False, "content": None}

    def with_options(self, **options):
        bounded = self.client.with_options(**options)

        def create(**kwargs):
            response = bounded.chat.completions.create(**kwargs)
            self.captured["received"] = True
            choices = getattr(response, "choices", None)
            if isinstance(choices, list) and len(choices) == 1:
                content = getattr(getattr(choices[0], "message", None), "content", None)
                if isinstance(content, str):
                    try:
                        if len(content) <= 65536 and len(content.encode("utf-8")) <= 65536:
                            self.captured["content"] = content
                        else:
                            self.captured["omitted_reason"] = "content_over_64KiB"
                    except UnicodeError:
                        self.captured["omitted_reason"] = "invalid_unicode"
            return response

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def validate_connection(base_url, api_key):
    url = urlsplit(base_url)
    if url.scheme not in ("http", "https") or url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise CheckError("local_proxy_required")
    if url.username or url.password or url.query or url.fragment or not isinstance(api_key, str) or not api_key:
        raise CheckError("invalid_model_connection_settings")


def run_probe(root, parent, *, confirmed, client_factory, base_url, api_key, sdk_version):
    if confirmed is not True:
        raise CheckError("live_confirmation_required")
    validate_connection(base_url, api_key)
    fixtures, adapter = load_modules(root)
    materials = fixtures.materials()
    request = fixtures.analysis.build_analysis_request(materials)
    if request is None:
        raise CheckError("synthetic_request_unavailable")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    directory = Path(tempfile.mkdtemp(prefix="model-check-" + stamp + "-", dir=parent))
    save(directory, "materials.json", materials)
    save(directory, "request.json", request)
    save(directory, "output-schema.json", fixtures.analysis.AnalysisDraft.model_json_schema())
    save(
        directory,
        "manifest.json",
        {
            "schema_version": "sec_know.rule_model_check.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data_source": "synthetic_fixture",
            "model_transport": "live",
            "requested_model": MODEL,
            "base_url": base_url,
            "sdk_version": sdk_version,
            "timeout_seconds": 240,
            "sdk_max_retries": 0,
            "extra_body": {"timeout": 240, "need_trans": True},
            "upstream_attempts": None,
            "source_sha256": EXPECTED,
            "prompt_sha256": request["prompt_sha256"],
            "probe_version": "step04d-v1",
            "operator_confirmation": "LIVE",
            "authority": "advice_only_no_delivery_or_rule_change",
        },
    )
    print("结果保存目录：" + str(directory), flush=True)
    print("开始请求真实模型，使用人工材料；SDK 超时设置为240秒，请等待。", flush=True)
    client = client_factory(base_url=base_url, api_key=api_key, max_retries=0, timeout=240)
    capture = CaptureClient(client)
    try:
        result = adapter.analyze_with_model(materials, capture, model=MODEL, transport_mode="live")
        save(directory, "response-content.json", capture.captured)
        save(directory, "result.json", result)
    finally:
        client.close()
    audit = result["model_transport"]
    summary = {
        "check": "rule_refine_live_model",
        "material_source": "synthetic_fixture",
        "status": result["status"],
        "format_valid": audit["sdk_request_calls"] == 1
        and result["status"] in ("proposal", "no_change", "insufficient_evidence", "needs_existing_condition_change"),
        "requested_model": MODEL,
        "returned_model": audit["returned_model"],
        "sdk_request_calls": audit["sdk_request_calls"],
        "upstream_attempts": None,
        "finish_reason": audit["finish_reason"],
        "usage": audit["usage"],
        "elapsed_ms": audit["elapsed_ms"],
        "error_code": audit["error_code"] or result.get("error_code"),
        "error_type": audit.get("error_type") or result.get("error_type"),
        "validation_scope": result["validation_scope"],
        "engine_compatibility": result["engine_compatibility"],
        "output_dir": str(directory),
    }
    save(directory, "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def confirm_live(base_url):
    print("试调用预览：模型=" + MODEL + "；代理=" + base_url, flush=True)
    print("使用人工材料，发起一次真实SDK请求；代理内部可能重试。", flush=True)
    print("确认执行请输入 LIVE，其他输入退出：", end="", flush=True)
    with open("/dev/tty", "r", encoding="utf-8") as terminal:
        return terminal.readline().strip() == "LIVE"


def main():
    # 代理启动脚本和Python读取的环境变量名不同，两者均要求LOCAL。
    if any(os.environ.get(name, "LOCAL").upper() != "LOCAL" for name in ("env_profile", "ENV_PROFILE")):
        print("停止：本次检查只支持LOCAL；请保持现有服务配置，贴回这条提示。")
        return 2
    root = Path.cwd()
    load_modules(root)
    import httpx
    from openai import OpenAI

    import util.root_config as rc
    from nas.config import APP_KEY_DICT

    validate_connection(rc.LITELLM_OPENAI_SERVER, APP_KEY_DICT.get("litellm_key"))
    if not confirm_live(rc.LITELLM_OPENAI_SERVER):
        print("未确认真实调用，已退出。")
        return 0
    # 本机请求不经过终端中的HTTP代理，不改变现有服务或代理配置。
    with httpx.Client(trust_env=False, timeout=240) as http_client:
        result = run_probe(
            root,
            Path.home() / "sec-rule-refine-data",
            confirmed=True,
            client_factory=lambda **options: OpenAI(http_client=http_client, **options),
            base_url=rc.LITELLM_OPENAI_SERVER,
            api_key=APP_KEY_DICT.get("litellm_key"),
            sdk_version=importlib.metadata.version("openai"),
        )
    return 0 if result["format_valid"] else 2


if __name__ == "__main__":
    try:
        code = main()
    except Exception as error:
        print(
            json.dumps(
                {
                    "check": "rule_refine_live_model",
                    "status": "check_failed",
                    "error_code": str(error) if isinstance(error, CheckError) else "check_exception",
                    "error_type": type(error).__name__,
                }
            )
        )
        print("未自动重试；若已打印结果目录，请保留该目录并贴回终端摘要。")
        code = 2
    sys.exit(code)
PYLIVE
SOC_STEP04D
