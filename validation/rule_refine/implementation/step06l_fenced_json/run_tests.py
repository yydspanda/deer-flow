"""Assemble the installed module versions in a temporary tree for offline tests."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix="rule-fenced-json-tests-") as temporary:
    root = Path(temporary)
    for step in (
        "step01_contracts",
        "step03d_preparation",
        "step04a_materials",
        "step06c_task_source",
        "step04b_analysis",
        "step06l_fenced_json",
    ):
        payload = HERE.parent / step / "payload"
        for source in payload.rglob("*.py"):
            target = root / source.relative_to(payload)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    result = subprocess.run(
        [sys.executable, "-B", str(root / "test/rule_refine/test_analysis.py"), *sys.argv[1:]],
        check=False,
    )
raise SystemExit(result.returncode)
