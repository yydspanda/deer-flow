#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/sec_know_model"

.venv/bin/python - <<'PY'
import json
from pathlib import Path

files = [
    "config/rule_refine.local.json",
    "config/rule_refine.analysis.local.json",
    "config/rule_refine.limits.local.json",
]
for name in files:
    path = Path(name)
    if not path.is_file():
        raise SystemExit("缺少配置文件，未修改.gitignore：" + name)
    json.loads(path.read_text(encoding="utf-8"))

ignore = Path(".gitignore")
original = ignore.read_text(encoding="utf-8")
marker = "# Track shared rule-refine settings"
owned_lines = {marker}
for name in files:
    owned_lines.update({name, "/" + name, "!" + name, "!/" + name})
lines = [line for line in original.splitlines() if line.strip() not in owned_lines]
updated = "\n".join(lines).rstrip("\n") + "\n\n" + marker + "\n"
updated += "".join("!/" + name + "\n" for name in files)
if updated != original:
    ignore.write_text(updated, encoding="utf-8")

print("已允许Git跟踪三个规则分析配置；配置内容保持原样。")
PY

git add -- .gitignore \
  config/rule_refine.local.json \
  config/rule_refine.analysis.local.json \
  config/rule_refine.limits.local.json

git diff --cached --stat -- .gitignore \
  config/rule_refine.local.json \
  config/rule_refine.analysis.local.json \
  config/rule_refine.limits.local.json
