"""Build a complete readable one-command handoff; no encoded source payload."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
install = (HERE / "install.py").read_text(encoding="utf-8")
assert install.count("from changes import PATCHES\n") == 1
install = install.replace("from changes import PATCHES\n", "", 1)
code = (HERE / "changes.py").read_text(encoding="utf-8") + "\n" + install
compile(code, "<install-parser-fix>", "exec")
(HERE / "APPLY-PARSER-FIX.sh").write_text(
    "bash <<'SOC_APPLY_PARSER'\nset -euo pipefail\numask 077\n"
    "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_APPLY'\n"
    + code
    + "PY_APPLY\nSOC_APPLY_PARSER\n",
    encoding="utf-8",
)
pins = json.loads(
    (HERE.parent / "step06j_synthetic_chain/pins.json").read_text(encoding="utf-8")
)
pins["my_workflows/zeus/rule_refine/analysis.py"] = (
    "c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0"
)
(HERE / "current-pins.json").write_text(
    json.dumps(pins, indent=2) + "\n", encoding="utf-8"
)
print("Built APPLY-PARSER-FIX.sh and current-pins.json")
