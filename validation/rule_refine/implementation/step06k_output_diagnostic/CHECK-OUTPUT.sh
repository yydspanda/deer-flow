bash <<'SOC_OUTPUT_CHECK'
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
cat > "$soc_diag_work/diagnostics.py" <<'PY_DIAG'
"""One-process diagnostics for the synthetic check; preserve validation semantics."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType

from pydantic import ValidationError

ANALYSIS_SHA = "1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117"
REASONS = {
    "回答需要有界JSON文本",
    "回答含重复JSON字段",
    "回答必须使用标准JSON数值",
    "回答引用了未展示样本",
    "建议topic与规则不一致",
    "条件缺少所引样本支持",
    "样本引用不能重复",
    "新增排除建议需要条件和样本",
    "非建议状态不能携带过滤条件",
    "表达式含不支持的字符",
    "表达式过长",
    "括号位置不正确或嵌套过深",
    "括号不匹配或缺少条件",
    "运算符前缺少条件",
    "缺少运算符或引用了未定义条件",
    "表达式不完整",
    "包含/不包含条件的值不能为空",
    "需要1至32个条件，别名使用大写字母开头的字母、数字或下划线",
    "统计结束时间必须晚于开始时间",
    "样本告警ID不能重复",
    "样本数不能大于统计告警数",
    "automatic_apply必须为布尔值false",
}
FIELDS = set(
    "status issue recommendation certainty sample_alert_ids filter topic expression name value operator evidence window_start window_end alert_count count_basis company_code rule_code rule_name rule_config_version automatic_apply change_type recommendation_id schema_version".split()
)


def format_error(error):
    detail = {"error_type": type(error).__name__}
    if isinstance(error, json.JSONDecodeError):
        detail.update(
            category="json",
            line=error.lineno,
            column=error.colno,
            position=error.pos,
            fenced=error.doc.strip().startswith("```"),
        )
    elif isinstance(error, ValidationError):
        rows = error.errors(
            include_url=False, include_input=False, include_context=True
        )
        detail.update(category="schema", error_count=len(rows), errors=[])
        for row in rows[:8]:
            loc = [
                part
                if type(part) is int
                or isinstance(part, str)
                and (part in FIELDS or re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", part))
                else "<other_field>"
                for part in row.get("loc", ())[:8]
            ]
            item = {"type": row["type"], "loc": loc}
            reason = str(row.get("ctx", {}).get("error", ""))
            if reason in REASONS:
                item["reason"] = reason
            detail["errors"].append(item)
    else:
        detail["category"] = "output_check"
        if str(error) in REASONS:
            detail["reason"] = str(error)
    return detail


def write_private(folder, name, raw):
    fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def install(prepared, folder):
    relative = "my_workflows/zeus/rule_refine/analysis.py"
    raw = (prepared.root / relative).read_bytes()
    if (
        prepared.pins[relative] != ANALYSIS_SHA
        or hashlib.sha256(raw).hexdigest() != ANALYSIS_SHA
    ):
        raise ValueError("analysis_source_version_mismatch")
    before = """    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return {**result, "status": "analysis_failed", "recommendation": None, "error_code": "invalid_model_output"}"""
    after = """    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        return {**result, "status": "analysis_failed", "recommendation": None, "error_code": "invalid_model_output", "validation_error": _diagnostic_error(error)}"""
    source = raw.decode("utf-8")
    if source.count(before) != 1:
        raise ValueError("analysis_diagnostic_location_mismatch")
    source = source.replace(before, after, 1)
    original = prepared.modules.analysis
    module = ModuleType(original.__name__ + "_diagnostic")
    module.__package__ = original.__package__
    module._diagnostic_error = format_error
    sys.modules[module.__name__] = module
    exec(
        compile(source, str(folder / "analysis-diagnostic.py"), "exec"), module.__dict__
    )

    def analyze(materials, generate, *, response_source="synthetic_reply"):
        def capture(messages):
            answer = generate(messages)
            if isinstance(answer, str):
                try:
                    encoded = answer.encode("utf-8")
                except UnicodeError:
                    encoded = None
                if encoded is not None and len(encoded) <= 16384:
                    write_private(folder, "assistant-content.txt", encoded)
            return answer

        result = module.analyze_materials(
            materials, capture, response_source=response_source
        )
        write_private(
            folder,
            "output-check.json",
            json.dumps(
                {
                    "analysis_status": result["status"],
                    "error_code": result.get("error_code"),
                    "response_sha256": result.get("response_sha256"),
                    "validation_error": result.get("validation_error"),
                    "assistant_content_saved": (
                        folder / "assistant-content.txt"
                    ).is_file(),
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        return result

    write_private(
        folder,
        "diagnostic-manifest.json",
        json.dumps(
            {
                "material_source": "synthetic_fixture",
                "analysis_source_sha256": ANALYSIS_SHA,
                "diagnostic_analysis_sha256": hashlib.sha256(
                    source.encode("utf-8")
                ).hexdigest(),
                "diagnostic_helper_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "changes": "exception_diagnostics_and_private_assistant_content_only",
                "prompt_template_changed": False,
                "validation_relaxed": False,
                "automatic_repair_or_model_retry": False,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
    )
    prepared.modules.model_adapter.analyze_materials = analyze
PY_DIAG
cat > "$soc_diag_work/entry.py" <<'PY_ENTRY'
"""Install diagnostic-only instrumentation after the existing LIVE confirmation."""

import json
from pathlib import Path

import diagnostics
import runner

original_execute = runner.execute


def execute(prepared, output):
    diagnostics.install(prepared, output.parent)
    return original_execute(prepared, output)


runner.execute = execute
code = runner.main()
check = Path(__file__).with_name("output-check.json")
if check.is_file():
    print("\n模型输出校验详情：", flush=True)
    print(check.read_text(encoding="utf-8"), flush=True)
else:
    print(
        json.dumps(
            {"output_check_saved": False, "model_request_attempted": "see_run_summary"}
        )
    )
raise SystemExit(code)
PY_ENTRY
printf '本次诊断目录：%s\n' "$soc_diag_work"
.venv/bin/python -B "$soc_diag_work/entry.py"
SOC_OUTPUT_CHECK
