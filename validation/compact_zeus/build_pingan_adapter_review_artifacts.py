#!/usr/bin/env python3
"""Build sensitive local Checkpoint B artifacts for PingAn Adapter review."""

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
from soc_agent.core import SocNormalizationService  # noqa: E402
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402

SCHEMA_VERSION = "soc.validation.pingan_adapter_checkpoint_b.v1"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "validation/compact_zeus/data/reviews/pingan-adapter-checkpoint-b"
)

REVIEW_SAMPLES = {
    "direct-nids-json": {
        "alert_id": 1976128,
        "expected_source_type": "nids",
        "expected_policy": "raw_message_first",
        "expected_parser": "pingan_json_object",
        "review_focus": "Direct JSON must be parsed as a complete object, not partial quoted KV.",
    },
    "prefixed-edr-json": {
        "alert_id": 1968376,
        "expected_source_type": "edr",
        "expected_policy": "raw_message_first",
        "expected_parser": "pingan_json_object",
        "review_focus": "Syslog prefix must be metadata; the complete EDR JSON object is primary evidence.",
    },
    "prefixed-threat-intel-json": {
        "alert_id": 1965919,
        "expected_source_type": "threat_intel",
        "expected_policy": "raw_message_first",
        "expected_parser": "pingan_json_object",
        "review_focus": "ThreatBook prefix must be metadata and nested threat-intelligence fields must remain visible.",
    },
    "no-message-siem-fallback": {
        "alert_id": 1965802,
        "expected_source_type": "siem",
        "expected_policy": "structured_fallback",
        "expected_parser": None,
        "review_focus": "No message exists; the complete selected structured event remains the evidence source.",
    },
}


def build_review_artifact(
    *,
    cohort: str,
    row: Mapping[str, Any],
    expectation: Mapping[str, Any],
) -> dict[str, Any]:
    payload = row["alert_full_data"]["alert_data"]
    inspection = SocNormalizationService().inspect(payload)
    request = build_analysis_request_for_payload(
        payload,
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    alert = inspection.alert
    parsed_messages = alert.extensions.get("parsed_raw_messages", [])
    policy = alert.extensions.get("evidence_input_policy", {})

    parser_names = [
        str(item["parser_name"])
        for item in parsed_messages
        if isinstance(item, Mapping) and item.get("parser_name")
    ]
    actual_parser = parser_names[0] if parser_names else None
    actual = {
        "source_type": alert.source.source_type.value,
        "policy": policy.get("name"),
        "primary_parser": actual_parser,
    }
    expected = {
        "source_type": expectation["expected_source_type"],
        "policy": expectation["expected_policy"],
        "primary_parser": expectation["expected_parser"],
    }
    if actual != expected:
        raise AssertionError(
            f"alert_id={row['alert_id']} checkpoint mismatch: expected={expected!r}, actual={actual!r}"
        )

    canonical = {
        "schema_version": alert.schema_version,
        "alert_id": alert.alert_id,
        "source": alert.source.model_dump(mode="json", exclude_none=True),
        "detection": alert.detection.model_dump(mode="json", exclude_none=True),
        "event": alert.event.model_dump(mode="json", exclude_none=True),
        "classification": alert.classification.model_dump(
            mode="json",
            exclude_none=True,
        ),
        "entities": alert.entities.model_dump(mode="json", exclude_none=True),
        "evidence": [
            item.model_dump(mode="json", exclude_none=True) for item in alert.evidence
        ],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "cohort": cohort,
        "review_focus": expectation["review_focus"],
        "alert_id": int(row["alert_id"]),
        "topic": row.get("topic"),
        "topic_name": row.get("topic_name"),
        "expectation": expected,
        "actual": actual,
        "raw_preservation": {
            "preserved_in_alert_input": alert.raw == payload,
            "canonical_payload_sha256": row.get("canonical_payload_sha256"),
            "source_refs": row.get("source_refs"),
        },
        "evidence_input_policy": policy,
        "parsed_raw_messages": parsed_messages,
        "parsed_field_schema": [
            {
                "source_path": item.get("source_path"),
                "parser_name": item.get("parser_name"),
                "fields": _field_schema(item.get("fields")),
                "decoded_fields": _field_schema(item.get("decoded_fields")),
                "repaired_fields": _field_schema(item.get("repaired_fields")),
            }
            for item in parsed_messages
            if isinstance(item, Mapping)
        ],
        "canonical_alert_without_raw": canonical,
        "normalization_report": inspection.normalization_report.model_dump(
            mode="json",
            exclude_none=True,
        ),
        "extracted_entities": inspection.entities.model_dump(
            mode="json",
            exclude_none=True,
        ),
        "fact_reconstruction": request.fact_reconstruction.model_dump(
            mode="json",
            exclude_none=True,
        ),
        "source_field_semantics": [
            item.model_dump(mode="json", exclude_none=True)
            for item in request.source_field_semantics
        ],
        "evidence_coverage": request.evidence_coverage.model_dump(
            mode="json",
            exclude_none=True,
        ),
        "bounded_analysis_evidence": {
            "primary": (
                request.primary_evidence.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                if request.primary_evidence is not None
                else None
            ),
            "supplementary": [
                item.model_dump(mode="json", exclude_none=True)
                for item in request.supplementary_evidence
            ],
        },
    }


def _field_schema(value: Any, path: str = "$") -> list[dict[str, str]]:
    entries = [{"path": path, "type": _type_name(value)}]
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            entries.extend(_field_schema(value[key], f"{path}.{key}"))
    elif isinstance(value, list) and value:
        entries.extend(_field_schema(value[0], f"{path}[]"))
    return entries


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame = load_dataframe_pickle(args.corpus)
    artifacts: list[dict[str, Any]] = []
    for cohort, expectation in REVIEW_SAMPLES.items():
        alert_id = expectation["alert_id"]
        matches = frame.loc[frame["alert_id"] == alert_id]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one corpus row for alert_id={alert_id}, found {len(matches)}"
            )
        artifact = build_review_artifact(
            cohort=cohort,
            row=matches.iloc[0].to_dict(),
            expectation=expectation,
        )
        output_path = args.output_dir / f"{cohort}-{alert_id}.json"
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
        "corpus": str(args.corpus.resolve().relative_to(ROOT)),
        "sensitive_local_artifacts": True,
        "artifacts": artifacts,
    }
    _write_json(args.output_dir / "index.json", index)
    print(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
