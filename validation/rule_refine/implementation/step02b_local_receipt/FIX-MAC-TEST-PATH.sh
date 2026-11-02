bash <<'SOC_FIX_PATH'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYFIX'
from pathlib import Path

target = Path("test/rule_refine/test_local_sink.py")
text = target.read_text(encoding="utf-8")
old = '        self.directory = Path(directory.name) / "receipts"'
new = '        self.directory = Path(directory.name).resolve() / "receipts"'
if text.count(old) == 1 and text.count(new) == 0:
    updated = text.replace(old, new, 1)
    compile(updated, str(target), "exec")
    target.write_text(updated, encoding="utf-8")
    print("已修复：测试临时目录统一为真实路径。")
elif text.count(old) == 0 and text.count(new) == 1:
    print("已修复，无需重复修改。")
else:
    raise SystemExit("停止：测试文件内容与预期不同，未修改，请贴回输出。")
PYFIX
.venv/bin/python test/rule_refine/test_local_sink.py
.venv/bin/python test/rule_refine/test_local_sink.py \
  --demo-dir "$HOME/sec-rule-refine-data/receipt-demo"
SOC_FIX_PATH
