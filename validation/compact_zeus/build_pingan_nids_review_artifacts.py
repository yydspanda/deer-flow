#!/usr/bin/env python3
"""Build sensitive local before/after artifacts for PingAn NIDS review."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from validation.compact_zeus.build_pingan_adapter_review_artifacts import (  # noqa: E402
    build_review_artifact,
)
from validation.compact_zeus.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

SCHEMA_VERSION = "soc.validation.pingan_nids_checkpoint_c.v1"
DEFAULT_CORPUS_PATH = ROOT / "validation/compact_zeus/data/full_alert_validation_corpus.pkl"
DEFAULT_OUTPUT_DIR = ROOT / "validation/compact_zeus/data/pingan-nids-checkpoint-c"

REVIEW_SAMPLES = {
    "structured-http": {
        "alert_id": 1976128,
        "review_focus": ("Verify five-tuple, nested sensor detection, HTTP request/response, flow direction, role claims, and scenario signals."),
    },
    "header-string-only": {
        "alert_id": 1985831,
        "review_focus": ("No structured HTTP object is available; verify whether bounded request/response header strings provide deterministic HTTP fields."),
    },
    "query-context": {
        "alert_id": 1970445,
        "review_focus": ("Verify the generic query field remains bounded evidence and is not mislabeled as DNS or promoted to a canonical domain without protocol evidence."),
    },
    "multiple-messages": {
        "alert_id": 1979525,
        "review_focus": ("Verify primary and supplementary messages remain separate observations and are not collapsed into one synthetic five-tuple."),
    },
}


def build_nids_review_artifact(
    *,
    cohort: str,
    row: Mapping[str, Any],
    review_focus: str,
    phase: str,
) -> dict[str, Any]:
    artifact = build_review_artifact(
        cohort=cohort,
        row=row,
        expectation={
            "expected_source_type": "nids",
            "expected_policy": "raw_message_first",
            "expected_parser": "pingan_json_object",
            "review_focus": review_focus,
        },
    )
    return {
        **artifact,
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--phase",
        choices=("before_adapter_mapping", "after_adapter_mapping"),
        default="before_adapter_mapping",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame = load_dataframe_pickle(args.corpus)
    artifacts: list[dict[str, Any]] = []
    phase_dir = args.output_dir / args.phase
    for cohort, definition in REVIEW_SAMPLES.items():
        alert_id = definition["alert_id"]
        matches = frame.loc[frame["alert_id"] == alert_id]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one corpus row for alert_id={alert_id}, found {len(matches)}")
        artifact = build_nids_review_artifact(
            cohort=cohort,
            row=matches.iloc[0].to_dict(),
            review_focus=str(definition["review_focus"]),
            phase=args.phase,
        )
        output_path = phase_dir / f"{cohort}-{alert_id}.json"
        _write_json(output_path, artifact)
        artifacts.append(
            {
                "cohort": cohort,
                "alert_id": alert_id,
                "path": str(output_path.resolve().relative_to(ROOT)),
                "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "actual": artifact["actual"],
            }
        )

    index = {
        "schema_version": SCHEMA_VERSION,
        "phase": args.phase,
        "corpus": str(args.corpus.resolve().relative_to(ROOT)),
        "sensitive_local_artifacts": True,
        "artifacts": artifacts,
    }
    _write_json(phase_dir / "index.json", index)
    print(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
