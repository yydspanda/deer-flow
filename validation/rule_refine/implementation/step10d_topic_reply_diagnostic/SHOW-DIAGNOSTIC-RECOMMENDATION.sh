bash <<'SOC_SHOW_TOPIC_DIAGNOSTIC'
set -euo pipefail
python3 -B - <<'PY_SHOW_TOPIC_DIAGNOSTIC'
import json
import os
from pathlib import Path

base = Path(os.environ.get(
    "TOPIC_DIAGNOSTIC_DIR",
    str(Path.home() / "sec_know_model/nas/data/rule_refine/topic_reply_diagnostics/diagnostic-fai0u98o/analysis"),
)).expanduser()
summary = json.loads((base / "diagnostic-summary.json").read_text(encoding="utf-8"))
job_id = summary["job_id"]
if not isinstance(job_id, str) or len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
    raise SystemExit("诊断记录的job_id格式不正确，未继续读取。")
folder = base / "jobs" / job_id
candidate = json.loads((folder / "recommendation.json").read_text(encoding="utf-8"))
phases = []
for phase in ("generate", "review"):
    record = json.loads((folder / (phase + ".summary.json")).read_text(encoding="utf-8"))
    phases.append({"phase": phase, "selection": record.get("selection"), "json_diagnostics": record.get("json_diagnostics")})
print(json.dumps({
    "check": "show_saved_topic_diagnostic_recommendation",
    "read_only": True,
    "job_id": job_id,
    "analysis_status": candidate.get("analysis_status"),
    "recommendation_ready": candidate.get("recommendation_ready"),
    "filter": candidate.get("filter"),
    "executeType": candidate.get("executeType"),
    "explanation": candidate.get("explanation"),
    "phases": phases,
    "model_calls": 0,
    "zeus_posts": 0,
    "files_written": 0,
}, ensure_ascii=False, indent=2))
PY_SHOW_TOPIC_DIAGNOSTIC
SOC_SHOW_TOPIC_DIAGNOSTIC
