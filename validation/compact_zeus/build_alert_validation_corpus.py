#!/usr/bin/env python3
"""Build one canonical Zeus alert validation corpus from PKL and legacy JSON.

The source DataFrame remains authoritative for alert IDs it already contains.
Legacy JSON demos are wrapped as ``alert_full_data`` rows only when missing.
Exact duplicates become lineage, while conflicting legacy payloads are retained
under ``legacy_demo_variants`` without creating duplicate alert IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from validation.compact_zeus.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.contracts import (  # noqa: E402
    EvidenceLayer,
    EvidenceTrustLevel,
    SensitiveEvidenceMode,
)
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402
from soc_agent.pipeline.evidence_coverage import observe_message_schemas  # noqa: E402

CORPUS_SCHEMA_VERSION = "soc.validation.alert_corpus.v1"
DEFAULT_SOURCE_PATH = ROOT / "datas/source/full_alert_2026_month_forth_sample_200.pkl"
DEFAULT_DEMO_DIR = ROOT / "datas/legacy_demos"
DEFAULT_OUTPUT_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_MANIFEST_PATH = (
    ROOT
    / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.manifest.json"
)

PROVENANCE_COLUMNS = [
    "corpus_schema_version",
    "sample_origin",
    "source_refs",
    "canonical_payload_sha256",
    "legacy_demo_status",
    "legacy_demo_variants",
    "has_agent_response",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    serialized = json.dumps(
        _json_compatible(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_legacy_demos(demo_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    demos: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(demo_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError(f"{path}: expected JSON object")
        alert = payload.get("alert")
        if not isinstance(alert, dict) or not isinstance(alert.get("hitLog"), list):
            raise ValueError(f"{path}: expected PingAn alert.hitLog[] payload")
        alert_id = _alert_id(payload)
        filename_id = _filename_alert_id(path)
        if filename_id is not None and filename_id != alert_id:
            raise ValueError(
                f"{path}: filename alert ID {filename_id} != payload {alert_id}"
            )
        demos.append((path, payload))
    if not demos:
        raise ValueError(f"no legacy JSON demos found in {demo_dir}")
    return demos


def build_corpus(
    source_frame: pd.DataFrame,
    *,
    source_ref: str,
    demos: Sequence[tuple[Path, dict[str, Any]]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Merge source rows and legacy demos without changing source canonical rows."""

    _validate_source_frame(source_frame)
    source_columns = list(source_frame.columns)
    corpus = source_frame.copy(deep=True).reset_index(drop=True)

    corpus["corpus_schema_version"] = CORPUS_SCHEMA_VERSION
    corpus["sample_origin"] = "full_alert_sample"
    corpus["source_refs"] = [
        [f"{source_ref}#alert_id={int(alert_id)}"] for alert_id in corpus["alert_id"]
    ]
    corpus["canonical_payload_sha256"] = [
        canonical_sha256(value) for value in corpus["alert_full_data"]
    ]
    corpus["legacy_demo_status"] = "not_provided"
    corpus["legacy_demo_variants"] = [[] for _ in range(len(corpus))]
    corpus["has_agent_response"] = [
        isinstance(value, str) and bool(value.strip())
        for value in corpus["agent_response"]
    ]

    index_by_alert_id = {
        int(alert_id): index for index, alert_id in enumerate(corpus["alert_id"])
    }
    demo_records: list[dict[str, Any]] = []
    appended_rows: list[dict[str, Any]] = []

    for demo_path, demo_payload in demos:
        alert_id = _alert_id(demo_payload)
        demo_ref = _relative_path(demo_path)
        demo_hash = canonical_sha256(demo_payload)

        if alert_id not in index_by_alert_id:
            row = _legacy_demo_row(
                demo_payload,
                source_columns=source_columns,
                source_ref=demo_ref,
            )
            appended_rows.append(row)
            demo_records.append(
                {
                    "alert_id": alert_id,
                    "source_ref": demo_ref,
                    "status": "appended",
                    "demo_payload_sha256": demo_hash,
                    "canonical_payload_sha256": row["canonical_payload_sha256"],
                    "difference_paths": [],
                }
            )
            continue

        row_index = index_by_alert_id[alert_id]
        canonical_full_data = corpus.at[row_index, "alert_full_data"]
        canonical_alert_data = _alert_data(canonical_full_data, alert_id=alert_id)
        canonical_hash = canonical_sha256(canonical_alert_data)
        source_refs = list(corpus.at[row_index, "source_refs"])
        source_refs.append(demo_ref)
        corpus.at[row_index, "source_refs"] = source_refs

        if demo_payload == canonical_alert_data:
            status = "exact_match"
            difference_paths: list[str] = []
        else:
            status = "conflict_pkl_authoritative"
            difference_paths = deep_difference_paths(
                demo_payload,
                canonical_alert_data,
            )
            variants = list(corpus.at[row_index, "legacy_demo_variants"])
            variants.append(
                {
                    "source_ref": demo_ref,
                    "payload_sha256": demo_hash,
                    "difference_paths": difference_paths,
                    "alert_data": demo_payload,
                }
            )
            corpus.at[row_index, "legacy_demo_variants"] = variants
        corpus.at[row_index, "legacy_demo_status"] = status

        demo_records.append(
            {
                "alert_id": alert_id,
                "source_ref": demo_ref,
                "status": status,
                "demo_payload_sha256": demo_hash,
                "canonical_payload_sha256": canonical_hash,
                "difference_paths": difference_paths,
            }
        )

    if appended_rows:
        corpus = pd.concat(
            [corpus, pd.DataFrame(appended_rows, columns=corpus.columns)],
            ignore_index=True,
        )

    for column, dtype in source_frame.dtypes.items():
        corpus[column] = corpus[column].astype(dtype)
    for column in PROVENANCE_COLUMNS:
        corpus[column] = corpus[column].astype(object)
    # Pandas 3 may infer a StringArray-backed column Index. Keep the output
    # compatible with the narrow restricted-unpickler policy used for samples.
    corpus.columns = pd.Index(corpus.columns.tolist(), dtype=object)
    _assert_source_rows_preserved(
        source_frame,
        corpus,
        source_columns=source_columns,
    )

    report = {
        "source_rows": len(source_frame),
        "source_unique_alert_ids": int(source_frame["alert_id"].nunique()),
        "legacy_demo_count": len(demo_records),
        "legacy_demo_status_counts": dict(
            sorted(Counter(item["status"] for item in demo_records).items())
        ),
        "legacy_demos": demo_records,
        "appended_alert_ids": sorted(
            item["alert_id"] for item in demo_records if item["status"] == "appended"
        ),
        "conflicting_alert_ids": sorted(
            item["alert_id"]
            for item in demo_records
            if item["status"] == "conflict_pkl_authoritative"
        ),
        "output_rows": len(corpus),
        "output_unique_alert_ids": int(corpus["alert_id"].nunique()),
        "source_rows_preserved": True,
    }
    return corpus, report


def validate_corpus(corpus: pd.DataFrame) -> dict[str, Any]:
    """Validate wrapper, metadata, response, lineage, and uniqueness invariants."""

    errors: list[dict[str, Any]] = []
    metadata_mismatches: Counter[str] = Counter()
    response_counts: Counter[str] = Counter()

    duplicate_ids = sorted(
        int(value)
        for value in corpus.loc[
            corpus["alert_id"].duplicated(keep=False),
            "alert_id",
        ].unique()
    )
    if duplicate_ids:
        errors.append({"kind": "duplicate_alert_ids", "alert_ids": duplicate_ids})

    for _, row in corpus.iterrows():
        alert_id = int(row["alert_id"])
        full_data = row["alert_full_data"]
        if not isinstance(full_data, dict):
            errors.append(
                {
                    "kind": "alert_full_data_not_object",
                    "alert_id": alert_id,
                }
            )
            continue
        required_wrapper_keys = {"app_code", "flow_id", "alert_id", "alert_data"}
        if not required_wrapper_keys.issubset(full_data):
            errors.append(
                {
                    "kind": "wrapper_keys_missing",
                    "alert_id": alert_id,
                    "keys": sorted(required_wrapper_keys.difference(full_data)),
                }
            )
            continue
        if str(full_data["alert_id"]) != str(alert_id):
            errors.append(
                {
                    "kind": "wrapper_alert_id_mismatch",
                    "alert_id": alert_id,
                }
            )
        alert_data = full_data["alert_data"]
        if not isinstance(alert_data, dict):
            errors.append(
                {
                    "kind": "alert_data_not_object",
                    "alert_id": alert_id,
                }
            )
            continue
        alert = alert_data.get("alert")
        if not isinstance(alert, dict) or str(alert.get("alertId")) != str(alert_id):
            errors.append(
                {
                    "kind": "payload_alert_id_mismatch",
                    "alert_id": alert_id,
                }
            )
            continue

        hit_log = alert.get("hitLog")
        first_hit = (
            hit_log[0]
            if isinstance(hit_log, list) and hit_log and isinstance(hit_log[0], dict)
            else {}
        )
        expected_metadata = {
            "risk_level": alert.get("riskLevel"),
            "status": alert.get("status"),
            "execute_type": alert.get("executeType"),
            "primary_type": alert.get("primaryType"),
            "secondary_type": alert.get("secondaryType"),
            "tertiary_type": alert.get("tertiaryType"),
            "topic": first_hit.get("topic"),
            "topic_name": first_hit.get("topicName"),
        }
        for column, expected in expected_metadata.items():
            if column in corpus.columns and row[column] != expected:
                metadata_mismatches[column] += 1

        response = row.get("agent_response")
        if _is_missing_scalar(response):
            response_counts["missing"] += 1
        elif not isinstance(response, str) or not response.strip():
            response_counts["invalid_type_or_empty"] += 1
            errors.append(
                {
                    "kind": "agent_response_invalid_type_or_empty",
                    "alert_id": alert_id,
                }
            )
        else:
            try:
                parsed_response = json.loads(response)
            except json.JSONDecodeError:
                response_counts["invalid_json"] += 1
                errors.append(
                    {
                        "kind": "agent_response_invalid_json",
                        "alert_id": alert_id,
                    }
                )
            else:
                if not isinstance(parsed_response, dict):
                    response_counts["non_object"] += 1
                    errors.append(
                        {
                            "kind": "agent_response_non_object",
                            "alert_id": alert_id,
                        }
                    )
                elif str(parsed_response.get("alert_id")) != str(alert_id):
                    response_counts["alert_id_mismatch"] += 1
                    errors.append(
                        {
                            "kind": "agent_response_alert_id_mismatch",
                            "alert_id": alert_id,
                        }
                    )
                else:
                    response_counts["valid"] += 1

        if row.get("corpus_schema_version") != CORPUS_SCHEMA_VERSION:
            errors.append(
                {
                    "kind": "corpus_schema_version_mismatch",
                    "alert_id": alert_id,
                }
            )
        if not isinstance(row.get("source_refs"), list) or not row["source_refs"]:
            errors.append({"kind": "source_refs_missing", "alert_id": alert_id})
        if canonical_sha256(full_data) != row.get("canonical_payload_sha256"):
            errors.append(
                {
                    "kind": "canonical_payload_hash_mismatch",
                    "alert_id": alert_id,
                }
            )

    if metadata_mismatches:
        errors.append(
            {
                "kind": "metadata_mismatch",
                "counts": dict(sorted(metadata_mismatches.items())),
            }
        )

    return {
        "status": "passed" if not errors else "failed",
        "rows": len(corpus),
        "unique_alert_ids": int(corpus["alert_id"].nunique()),
        "duplicate_alert_ids": duplicate_ids,
        "topic_counts": _value_counts(corpus, "topic"),
        "sample_origin_counts": _value_counts(corpus, "sample_origin"),
        "legacy_demo_status_counts": _value_counts(corpus, "legacy_demo_status"),
        "agent_response_counts": dict(sorted(response_counts.items())),
        "ground_label_non_null": _non_null_count(corpus, "ground_label"),
        "metadata_mismatch_counts": dict(sorted(metadata_mismatches.items())),
        "errors": errors,
    }


def validate_with_soc_normalizer(corpus: pd.DataFrame) -> dict[str, Any]:
    """Run every canonical payload through the production PingAn normalizer."""

    errors: list[dict[str, Any]] = []
    source_type_counts: Counter[str] = Counter()
    evidence_policy_counts: Counter[str] = Counter()
    schema_status_counts: Counter[str] = Counter()
    parser_counts: Counter[str] = Counter()
    topic_details: dict[str, Counter[str]] = {}
    policy_contract_violations: list[dict[str, Any]] = []
    alerts_with_degraded_schema = 0
    alerts_with_unsupported_schema = 0
    alerts_without_message_observation = 0
    structured_fallback_analysis_count = 0
    structured_fallback_unavailable_count = 0
    structured_fallback_projected_fields = 0
    structured_fallback_truncated_count = 0
    llm_projection_analysis_count = 0
    llm_encoded_compaction_alerts = 0
    llm_encoded_compaction_spans = 0
    llm_encoded_compaction_kinds: Counter[str] = Counter()
    llm_projection_source_details: dict[str, Counter[str]] = {}
    raw_payload_mutation_count = 0

    for _, row in corpus.iterrows():
        alert_id = int(row["alert_id"])
        topic = str(row.get("topic") or "unknown")
        topic_counter = topic_details.setdefault(topic, Counter())
        topic_counter["alerts"] += 1
        try:
            alert_data = _alert_data(row["alert_full_data"], alert_id=alert_id)
            input_hash_before = canonical_sha256(alert_data)
            normalized = normalize_alert_payload(alert_data)
            if str(normalized.alert_id) != str(alert_id):
                raise ValueError(
                    f"normalized alert_id={normalized.alert_id!r} != {alert_id!r}"
                )
            source_type = normalized.source.source_type.value
            source_type_counts[source_type] += 1
            topic_counter[f"source_type:{source_type}"] += 1
            request = build_analysis_request_for_payload(
                alert_data,
                sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
            )
            llm_projection_analysis_count += 1
            topic_counter["llm_projection:evaluated"] += 1
            source_projection = llm_projection_source_details.setdefault(
                source_type,
                Counter(),
            )
            source_projection["evaluated_alerts"] += 1
            bounded_evidence = [
                item
                for item in [
                    request.primary_evidence,
                    *request.supplementary_evidence,
                ]
                if item is not None
            ]
            compacted = [
                omission
                for evidence in bounded_evidence
                for omission in evidence.encoded_span_omissions
            ]
            if compacted:
                llm_encoded_compaction_alerts += 1
                llm_encoded_compaction_spans += len(compacted)
                llm_encoded_compaction_kinds.update(item.kind for item in compacted)
                topic_counter["llm_encoded_compaction:alerts"] += 1
                topic_counter["llm_encoded_compaction:spans"] += len(compacted)
                source_projection["compacted_alerts"] += 1
                source_projection["compacted_spans"] += len(compacted)
            if canonical_sha256(alert_data) != input_hash_before:
                raw_payload_mutation_count += 1
                policy_contract_violations.append(
                    {
                        "alert_id": alert_id,
                        "expected_policy": "immutable_raw_payload",
                        "actual_policy": "payload_mutated_during_llm_projection",
                    }
                )

            policy = normalized.extensions.get("evidence_input_policy", {})
            policy_name = (
                str(policy.get("name"))
                if isinstance(policy, Mapping) and policy.get("name")
                else "missing"
            )
            evidence_policy_counts[policy_name] += 1
            topic_counter[f"evidence_policy:{policy_name}"] += 1
            expected_policy_name = (
                "raw_message_first"
                if _has_non_empty_raw_message(alert_data)
                else "structured_fallback"
            )
            if policy_name != expected_policy_name:
                policy_contract_violations.append(
                    {
                        "alert_id": alert_id,
                        "expected_policy": expected_policy_name,
                        "actual_policy": policy_name,
                    }
                )
            if expected_policy_name == "structured_fallback":
                primary = request.primary_evidence
                structured_fallback_analysis_count += 1
                if primary is None and not _has_structured_raw_event(alert_data):
                    structured_fallback_unavailable_count += 1
                    topic_counter["structured_fallback:raw_event_unavailable"] += 1
                elif (
                    primary is None
                    or primary.layer is not EvidenceLayer.RAW_STRUCTURED
                    or primary.sensitive_evidence_mode is not SensitiveEvidenceMode.FULL
                ):
                    policy_contract_violations.append(
                        {
                            "alert_id": alert_id,
                            "expected_policy": "bounded_raw_structured_full",
                            "actual_policy": (
                                None
                                if primary is None
                                else {
                                    "layer": primary.layer.value,
                                    "sensitive_evidence_mode": (
                                        primary.sensitive_evidence_mode.value
                                    ),
                                }
                            ),
                        }
                    )
                else:
                    structured_fallback_projected_fields += len(
                        primary.projected_field_paths
                    )
                    structured_fallback_truncated_count += int(primary.truncated)
                    if primary.sanitized_field_paths:
                        policy_contract_violations.append(
                            {
                                "alert_id": alert_id,
                                "expected_policy": "full_mode_without_sanitization",
                                "actual_policy": {
                                    "sanitized_field_paths": (
                                        primary.sanitized_field_paths
                                    ),
                                },
                            }
                        )
                    if request.supplementary_evidence:
                        policy_contract_violations.append(
                            {
                                "alert_id": alert_id,
                                "expected_policy": "first_structured_event_only",
                                "actual_policy": {
                                    "supplementary_count": len(
                                        request.supplementary_evidence
                                    ),
                                },
                            }
                        )
                if (
                    topic.lower() == "t_gbd_zeus_data"
                    and isinstance(policy, Mapping)
                    and policy.get("trust_level") != EvidenceTrustLevel.HIGH.value
                ):
                    policy_contract_violations.append(
                        {
                            "alert_id": alert_id,
                            "expected_policy": "trusted_internal_siem_high",
                            "actual_policy": {
                                "trust_level": policy.get("trust_level"),
                            },
                        }
                    )

            observations = observe_message_schemas(normalized)
            if not observations:
                alerts_without_message_observation += 1
                topic_counter["message_schema:no_observation"] += 1
                continue
            statuses = {item.status.value for item in observations}
            if "degraded" in statuses:
                alerts_with_degraded_schema += 1
            if "unsupported" in statuses:
                alerts_with_unsupported_schema += 1
            for observation in observations:
                status = observation.status.value
                schema_status_counts[status] += 1
                topic_counter[f"message_schema:{status}"] += 1
                if observation.parser_name:
                    parser_counts[observation.parser_name] += 1
                    topic_counter[f"parser:{observation.parser_name}"] += 1
        except Exception as exc:
            errors.append(
                {
                    "alert_id": alert_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }
            )

    other_topics = sorted(
        topic
        for topic, counts in topic_details.items()
        if counts.get("source_type:other", 0)
    )
    return {
        "status": (
            "passed" if not errors and not policy_contract_violations else "failed"
        ),
        "normalized_alerts": len(corpus) - len(errors),
        "errors": errors,
        "policy_contract_violations": policy_contract_violations,
        "source_type_counts": dict(sorted(source_type_counts.items())),
        "evidence_policy_counts": dict(sorted(evidence_policy_counts.items())),
        "message_parser_counts": dict(sorted(parser_counts.items())),
        "message_schema_observation_counts": dict(sorted(schema_status_counts.items())),
        "alerts_with_degraded_message_schema": alerts_with_degraded_schema,
        "alerts_with_unsupported_message_schema": alerts_with_unsupported_schema,
        "alerts_without_message_observation": alerts_without_message_observation,
        "structured_fallback_analysis_count": structured_fallback_analysis_count,
        "structured_fallback_unavailable_count": (
            structured_fallback_unavailable_count
        ),
        "structured_fallback_projected_fields": (structured_fallback_projected_fields),
        "structured_fallback_truncated_count": structured_fallback_truncated_count,
        "llm_projection_analysis_count": llm_projection_analysis_count,
        "llm_encoded_compaction": {
            "alerts": llm_encoded_compaction_alerts,
            "spans": llm_encoded_compaction_spans,
            "kinds": dict(sorted(llm_encoded_compaction_kinds.items())),
            "source_type_details": {
                source_type: dict(sorted(counts.items()))
                for source_type, counts in sorted(llm_projection_source_details.items())
            },
        },
        "raw_payload_mutation_count": raw_payload_mutation_count,
        "topics_normalized_as_other": other_topics,
        "topic_details": {
            topic: dict(sorted(counts.items()))
            for topic, counts in sorted(topic_details.items())
        },
    }


def deep_difference_paths(left: Any, right: Any, *, path: str = "$") -> list[str]:
    """Return deterministic paths whose type, presence, length, or value differs."""

    differences: list[str] = []
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right), key=str):
            child_path = _child_path(path, str(key))
            if key not in left or key not in right:
                differences.append(child_path)
            else:
                differences.extend(
                    deep_difference_paths(left[key], right[key], path=child_path)
                )
        return differences
    if isinstance(left, list):
        if len(left) != len(right):
            differences.append(f"{path}.length")
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            differences.extend(
                deep_difference_paths(
                    left_item,
                    right_item,
                    path=f"{path}[{index}]",
                )
            )
        return differences
    if left != right:
        differences.append(path)
    return differences


def write_pickle_atomic(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")
    frame.to_pickle(temp_path, protocol=pickle.HIGHEST_PROTOCOL)
    temp_path.replace(output_path)

    reloaded = load_dataframe_pickle(output_path)
    if not frame.equals(reloaded):
        raise AssertionError("restricted-unpickler round trip changed corpus DataFrame")


def write_manifest_atomic(manifest: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")
    temp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(output_path)


def build_manifest(
    *,
    source_path: Path,
    demo_dir: Path,
    output_path: Path,
    merge_report: Mapping[str, Any],
    quality_report: Mapping[str, Any],
    normalizer_report: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "path": _relative_path(source_path),
            "sha256": sha256_file(source_path),
            "filename_row_count_hint": 200,
            "actual_rows": merge_report["source_rows"],
        },
        "legacy_demo_dir": _relative_path(demo_dir),
        "merge": dict(merge_report),
        "quality": dict(quality_report),
        "soc_normalizer": dict(normalizer_report),
        "output": {
            "path": _relative_path(output_path),
            "sha256": sha256_file(output_path),
            "rows": quality_report["rows"],
            "unique_alert_ids": quality_report["unique_alert_ids"],
        },
        "claim_boundaries": [
            "The source PKL is authoritative for alert IDs it already contains.",
            "Legacy conflict variants are preserved but never selected as canonical rows.",
            "agent_response is historical model output, not analyst ground truth.",
            "Normalizer success proves contract compatibility, not triage correctness.",
            "Raw alerts and generated PKL remain local sensitive validation data.",
        ],
    }


def _legacy_demo_row(
    payload: dict[str, Any],
    *,
    source_columns: Sequence[str],
    source_ref: str,
) -> dict[str, Any]:
    alert = payload["alert"]
    alert_id = _alert_id(payload)
    hit_logs = alert.get("hitLog")
    first_hit = (
        hit_logs[0]
        if isinstance(hit_logs, list) and hit_logs and isinstance(hit_logs[0], dict)
        else {}
    )
    full_data = {
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": str(alert_id),
        "alert_data": payload,
    }
    values: dict[str, Any] = {column: None for column in source_columns}
    values.update(
        {
            "alert_id": alert_id,
            "alert_full_data": full_data,
            "agent_response": None,
            "risk_level": alert.get("riskLevel"),
            "topic": first_hit.get("topic"),
            "topic_name": first_hit.get("topicName"),
            "related_status_dict": _related_statuses(payload),
            "status": alert.get("status"),
            "execute_type": alert.get("executeType"),
            "primary_type": alert.get("primaryType"),
            "secondary_type": alert.get("secondaryType"),
            "tertiary_type": alert.get("tertiaryType"),
            "corpus_schema_version": CORPUS_SCHEMA_VERSION,
            "sample_origin": "legacy_demo",
            "source_refs": [source_ref],
            "canonical_payload_sha256": canonical_sha256(full_data),
            "legacy_demo_status": "appended",
            "legacy_demo_variants": [],
            "has_agent_response": False,
        }
    )
    return values


def _validate_source_frame(frame: pd.DataFrame) -> None:
    required = {
        "alert_id",
        "alert_full_data",
        "agent_response",
        "risk_level",
        "topic",
        "topic_name",
        "related_status_dict",
        "status",
        "execute_type",
        "primary_type",
        "secondary_type",
        "tertiary_type",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"source DataFrame missing columns: {missing}")
    if frame["alert_id"].isna().any():
        raise ValueError("source DataFrame contains null alert_id")
    if frame["alert_id"].duplicated().any():
        raise ValueError("source DataFrame contains duplicate alert_id")


def _assert_source_rows_preserved(
    source_frame: pd.DataFrame,
    corpus: pd.DataFrame,
    *,
    source_columns: Sequence[str],
) -> None:
    source_projection = source_frame.reset_index(drop=True)[list(source_columns)]
    corpus_projection = corpus.iloc[: len(source_frame)][list(source_columns)]
    if not source_projection.equals(corpus_projection):
        raise AssertionError("source DataFrame rows changed while building corpus")


def _alert_id(payload: Mapping[str, Any]) -> int:
    alert = payload.get("alert")
    value = alert.get("alertId") if isinstance(alert, Mapping) else None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid alert.alertId: {value!r}") from exc


def _filename_alert_id(path: Path) -> int | None:
    candidate = path.stem.rsplit("-", maxsplit=1)[-1]
    try:
        return int(candidate)
    except ValueError:
        return None


def _alert_data(full_data: Any, *, alert_id: int) -> dict[str, Any]:
    if not isinstance(full_data, dict):
        raise TypeError(f"alert_id={alert_id}: alert_full_data must be dict")
    alert_data = full_data.get("alert_data")
    if not isinstance(alert_data, dict):
        raise TypeError(f"alert_id={alert_id}: alert_data must be dict")
    return alert_data


def _related_statuses(payload: Mapping[str, Any]) -> Counter[str]:
    related = payload.get("relatedAlertList")
    if not isinstance(related, list):
        return Counter()
    return Counter(
        str(item["status"])
        for item in related
        if isinstance(item, Mapping) and item.get("status") is not None
    )


def _has_non_empty_raw_message(payload: Mapping[str, Any]) -> bool:
    alert = payload.get("alert")
    if not isinstance(alert, Mapping):
        return False
    hit_logs = alert.get("hitLog")
    if not isinstance(hit_logs, list):
        return False
    for hit_log in hit_logs:
        if not isinstance(hit_log, Mapping):
            continue
        raw_logs = hit_log.get("zeusRawLogs")
        if not isinstance(raw_logs, list):
            continue
        for raw_log in raw_logs:
            if not isinstance(raw_log, Mapping):
                continue
            message = raw_log.get("message")
            if isinstance(message, str) and message.strip():
                return True
    return False


def _has_structured_raw_event(payload: Mapping[str, Any]) -> bool:
    alert = payload.get("alert")
    if not isinstance(alert, Mapping):
        return False
    hit_logs = alert.get("hitLog")
    if not isinstance(hit_logs, list):
        return False
    return any(
        isinstance(raw_log, Mapping)
        for hit_log in hit_logs
        if isinstance(hit_log, Mapping)
        for raw_log in (
            hit_log.get("zeusRawLogs")
            if isinstance(hit_log.get("zeusRawLogs"), list)
            else []
        )
    )


def _json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_compatible(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_compatible(child) for child in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_compatible(value.item())
        except (TypeError, ValueError):
            pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _child_path(path: str, key: str) -> str:
    if key.isidentifier():
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


def _value_counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    if column not in frame:
        return {}
    return {
        str(key): int(value)
        for key, value in frame[column]
        .fillna("<null>")
        .value_counts(dropna=False)
        .sort_index()
        .items()
    }


def _non_null_count(frame: pd.DataFrame, column: str) -> int:
    return int(frame[column].notna().sum()) if column in frame else 0


def _is_missing_scalar(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return isinstance(result, bool) and result


def _relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--demo-dir", type=Path, default=DEFAULT_DEMO_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_frame = load_dataframe_pickle(args.source)
    demos = load_legacy_demos(args.demo_dir)
    corpus, merge_report = build_corpus(
        source_frame,
        source_ref=_relative_path(args.source),
        demos=demos,
    )
    quality_report = validate_corpus(corpus)
    normalizer_report = validate_with_soc_normalizer(corpus)
    if quality_report["status"] != "passed":
        raise ValueError(
            f"corpus quality validation failed: {quality_report['errors']}"
        )
    if normalizer_report["status"] != "passed":
        raise ValueError(
            f"SOC normalizer validation failed: errors={normalizer_report['errors']}, policy_contract_violations={normalizer_report['policy_contract_violations']}"
        )

    write_pickle_atomic(corpus, args.output)
    manifest = build_manifest(
        source_path=args.source,
        demo_dir=args.demo_dir,
        output_path=args.output,
        merge_report=merge_report,
        quality_report=quality_report,
        normalizer_report=normalizer_report,
    )
    write_manifest_atomic(manifest, args.manifest)

    print(
        json.dumps(
            {
                "status": "passed",
                "output": _relative_path(args.output),
                "manifest": _relative_path(args.manifest),
                "rows": len(corpus),
                "unique_alert_ids": int(corpus["alert_id"].nunique()),
                "legacy_demo_status_counts": merge_report["legacy_demo_status_counts"],
                "normalizer_source_type_counts": normalizer_report[
                    "source_type_counts"
                ],
                "topics_normalized_as_other": normalizer_report[
                    "topics_normalized_as_other"
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
