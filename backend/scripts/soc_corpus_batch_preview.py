"""Generate a read-only two-batch preview; never invoke models or submit alerts."""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.demo.corpus_batch_preview import prepare_batch_preview  # noqa: E402
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl")
    parser.add_argument("--index", type=Path, help="Defaults to the source's .workbench-index.json")
    parser.add_argument("--output-dir", type=Path, default=BACKEND_ROOT / ".deer-flow/soc-validation/corpus-batch-preview")
    parser.add_argument("--example-alert-id", action="append", default=[], help="Include this alert's group in the human-readable preview")
    args = parser.parse_args(argv)
    output = args.output_dir.expanduser().resolve()
    if not any(output.is_relative_to(root) for root in (BACKEND_ROOT / ".deer-flow", REPO_ROOT / "validation/compact_zeus/data")):
        parser.error("output must be under ignored backend/.deer-flow or validation/compact_zeus/data")
    source = args.source.expanduser().resolve()
    index = args.index.expanduser().resolve() if args.index else source.with_suffix(".workbench-index.json")
    try:
        result = prepare_batch_preview(
            source_path=source,
            index_path=index,
            output_dir=output,
            expected_profile=asdict(PingAnSocMemoryProfile.identity),
            example_alert_ids=tuple(args.example_alert_id),
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "draft", "plan_id": result.plan_id, "counts": result.counts, "groups": len(result.groups), "model_calls": 0, "alerts_run": 0, "output_dir": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
