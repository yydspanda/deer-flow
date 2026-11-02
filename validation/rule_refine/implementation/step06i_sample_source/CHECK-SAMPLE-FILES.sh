#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PY'
import json
import stat
from pathlib import Path

root = Path.cwd()
rows = []
for relative in (
    "nas/data/apt_test.json",
    "nas/original/20250429预警全量数据.pkl",
    "nas/original/20250605预警全量数据.pkl",
):
    path = root / relative
    row = {"path": str(path)}
    try:
        info = path.stat()
        row.update(exists=True, regular_file=stat.S_ISREG(info.st_mode), size_bytes=info.st_size)
    except FileNotFoundError:
        row.update(exists=False)
    except OSError as error:
        row.update(error_type=type(error).__name__)
    rows.append(row)
print(json.dumps({
    "check": "rule_refine_sample_files",
    "contents_read": False,
    "model_calls": 0,
    "files": rows,
}, ensure_ascii=False, indent=2))
PY
