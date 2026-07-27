#!/usr/bin/env python3
"""Build sensitive local review artifacts for PingAn NDR/HIDS Checkpoint C."""

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

from validation.compact_zeus.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.contracts import SensitiveEvidenceMode  # noqa: E402
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402

SCHEMA_VERSION = "soc.validation.pingan_ndr_hids_checkpoint_c.v1"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "validation/compact_zeus/data/reviews/pingan-ndr-hids-checkpoint-c"
)

REVIEW_SAMPLES = {
    "ndr-reverse-shell": (2025642, "reverse connection roles versus wire direction"),
    "ndr-nested-http": (
        1965449,
        "nested HTTP bodies, request context and repair labels",
    ),
    "ndr-webshell-upload": (1978257, "HTTP and observed network-content file metadata"),
    "ndr-command-execution": (1974996, "multiple messages and scenario evidence"),
    "hids-web-command": (1965448, "process tree and default external-IP placeholder"),
    "hids-reverse-shell": (1981436, "event-scoped outbound connection from endpoint"),
    "hids-linux-backdoor": (1985655, "Linux process and artifact evidence"),
    "hids-windows-backdoor": (1980407, "Windows process and artifact evidence"),
    "hids-malicious-operation": (1973457, "actor, process and inbound source context"),
    "hids-honeypot": (1980265, "event-scoped inbound source and ports"),
    "hids-honey-file": (1983975, "file integrity artifact and process chain"),
}


def build_ndr_hids_review_artifact(
    *,
    cohort: str,
    row: Mapping[str, Any],
    review_focus: str,
) -> dict[str, Any]:
    payload = _alert_payload(row)
    alert = normalize_alert_payload(payload)
    request = build_analysis_request_for_payload(
        payload,
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "cohort": cohort,
        "review_focus": review_focus,
        "alert_id": alert.alert_id,
        "source": alert.source.model_dump(mode="json", exclude_none=True),
        "canonical_alert_without_raw": alert.model_dump(
            mode="json",
            exclude={"raw"},
            exclude_none=True,
        ),
        "parsed_raw_messages": alert.extensions.get("parsed_raw_messages", []),
        "source_field_semantics": alert.extensions.get("source_field_semantics", []),
        "extracted_entities": request.extracted_entities.model_dump(
            mode="json", exclude_none=True
        ),
        "canonical_field_provenance": [
            item.model_dump(mode="json", exclude_none=True)
            for item in request.fact_reconstruction.canonical_field_provenance
        ],
        "fact_reconstruction": request.fact_reconstruction.model_dump(
            mode="json", exclude_none=True
        ),
        "evidence_coverage": request.evidence_coverage.model_dump(
            mode="json", exclude_none=True
        ),
        "bounded_analysis_evidence": {
            "primary": (
                request.primary_evidence.model_dump(mode="json", exclude_none=True)
                if request.primary_evidence is not None
                else None
            ),
            "supplementary": [
                item.model_dump(mode="json", exclude_none=True)
                for item in request.supplementary_evidence
            ],
        },
    }


def _alert_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    wrapper = row.get("alert_full_data")
    if not isinstance(wrapper, Mapping):
        raise ValueError("alert_full_data must be an object")
    payload = wrapper.get("alert_data")
    if not isinstance(payload, Mapping):
        raise ValueError("alert_full_data.alert_data must be an object")
    return payload


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame = load_dataframe_pickle(args.corpus)
    artifacts: list[dict[str, Any]] = []
    for cohort, (alert_id, review_focus) in REVIEW_SAMPLES.items():
        matches = frame.loc[frame["alert_id"] == alert_id]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one corpus row for alert_id={alert_id}, "
                f"found {len(matches)}"
            )
        artifact = build_ndr_hids_review_artifact(
            cohort=cohort,
            row=matches.iloc[0].to_dict(),
            review_focus=review_focus,
        )
        output_path = args.output_dir / f"{cohort}-{alert_id}.json"
        _write_json(output_path, artifact)
        artifacts.append(
            {
                "cohort": cohort,
                "alert_id": alert_id,
                "path": str(output_path.resolve().relative_to(ROOT)),
                "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            }
        )
    index = {
        "schema_version": SCHEMA_VERSION,
        "corpus": str(args.corpus.resolve().relative_to(ROOT)),
        "sensitive_local_artifacts": True,
        "artifacts": artifacts,
    }
    _write_json(args.output_dir / "index.json", index)
    print(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
