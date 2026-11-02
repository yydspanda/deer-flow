"""Prepare one original historical request; never send it or dispatch work."""

import ast
import copy
import hashlib
import io
import json
import os
import tempfile
import warnings
from collections import Counter
from datetime import datetime, timezone
from numbers import Integral
from pathlib import Path

SAMPLE_SHA = "7dcf39081920a8531f7458149f1c98c9d256e297c62c10ee9d385c39a091f2cf"
CSV_SHA = "729b0892ce3bd455d09f8d8b93d40afcf2d78a6f28d25e7753b640c1ac7e1976"
TARGET = ("ptp-nids", "RPAADM_002627")


class PreparationError(ValueError):
    """Only fixed non-sensitive diagnostic codes belong in this exception."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def ast_shape(node):
    if isinstance(node, ast.AST):
        return [
            type(node).__name__,
            {
                field: ast_shape(value)
                for field, value in ast.iter_fields(node)
                if field != "type_params"
            },
        ]
    if isinstance(node, list):
        return [ast_shape(item) for item in node]
    if node is Ellipsis:
        return {"python_constant": "ellipsis"}
    return node


def request_schema_matches(actual_source, reviewed_source):
    def shape(source):
        classes = [
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef) and node.name == "WorkFlowTaskInput"
        ]
        if len(classes) != 1:
            raise ValueError("request_schema_not_unique")
        return ast_shape(classes[0])

    return shape(actual_source) == shape(reviewed_source)


def source_review(root, inventory, pins):
    matches = {}
    for name, expected in pins.items():
        try:
            raw = inventory.bounded_read(root / name, 1024 * 1024)
            matches[name] = hashlib.sha256(raw).hexdigest() == expected
        except (OSError, ValueError):
            matches[name] = False
    return {
        "matches_reviewed_source": matches,
        "callback_profile_for_local_on_disk": "STG"
        if matches.get("util/root_config.py")
        else "unknown",
        "callback_on_success_and_failure": True
        if matches.get("tasks/task_util.py")
        else None,
        "callback_disable_switch_found_in_reviewed_source": False,
        "api_auth_scheme": "Authorization: Bearer"
        if matches.get("service/dependencies.py")
        else "unknown",
        "running_api_and_worker_profile_verified": False,
        "callback_request_sent": False,
    }


def select(frame, *, inventory, rules, normalize, request_model):
    if not frame.columns.is_unique or "alert_full_data" not in frame.columns:
        raise ValueError("unsupported_columns")
    candidates, fingerprints, issues = [], {}, Counter()
    for position, value in enumerate(frame["alert_full_data"].iloc[:200]):
        try:
            if isinstance(value, str):
                if len(value.encode("utf-8")) > 2 * 1024 * 1024:
                    raise ValueError("row_over_budget")
                value = json.loads(value, object_pairs_hook=unique_object)
            if not isinstance(value, dict):
                raise ValueError("not_object")
            inventory.bounded_json_shape(value)
            wrapper_created = "alert_data" not in value
            data = value if wrapper_created else value["alert_data"]
            alert = data.get("alert") if isinstance(data, dict) else None
            identity = (
                inventory.text_id(alert.get("alertId"))
                if isinstance(alert, dict)
                else None
            )
            if identity is None:
                raise ValueError("invalid_alert_identity")
            # Never overwrite either existing identity to force the request to fit.
            if (
                not wrapper_created
                and inventory.text_id(value.get("alert_id")) != identity
            ):
                raise ValueError("identity_mismatch")
            if "alert_id" in frame.columns:
                column_identity = frame["alert_id"].iloc[position]
                # Pandas integer columns yield numpy.int64, not a built-in int.
                if isinstance(column_identity, Integral) and not isinstance(
                    column_identity, bool
                ):
                    column_identity = int(column_identity)
                if inventory.text_id(column_identity) != identity:
                    raise ValueError("dataframe_identity_mismatch")
            fingerprint = hashlib.sha256(encode(data)).hexdigest()
            fingerprints.setdefault(identity, set()).add(fingerprint)
            request = (
                {
                    "app_code": "zeus",
                    "flow_id": "alert_agent",
                    "alert_id": identity,
                    "alert_data": data,
                }
                if wrapper_created
                else value
            )
            if (
                request.get("app_code") != "zeus"
                or request.get("flow_id") != "alert_agent"
            ):
                raise ValueError("other_workflow")
            request_model.model_validate(copy.deepcopy(request))
            result = normalize(
                copy.deepcopy(request),
                rules,
                mode="live",
                received_at=datetime.now(timezone.utc),
            )
            if result["issues"] or result["status"] != "ready":
                issues["normalization_not_ready"] += 1
                continue
            observations = result["observations"]
            if not 1 <= len(observations) <= 8:
                issues["observation_count_out_of_scope"] += 1
                continue
            if not any(
                (row["topic"], row["rule_code"]) == TARGET for row in observations
            ):
                continue
            target_hits = [
                hit
                for hit in alert.get("hitLog", [])
                if isinstance(hit, dict)
                and (hit.get("topic"), hit.get("ruleCode")) == TARGET
            ]
            if not any(
                isinstance(hit.get("zeusRawLogs"), list)
                and any(
                    isinstance(log, dict) and log for log in hit["zeusRawLogs"][:100]
                )
                for hit in target_hits
            ):
                issues["target_has_no_raw_log"] += 1
                continue
            raw = encode(request)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("request_over_budget")
            candidates.append((identity, position, raw, observations, wrapper_created))
        except (ValueError, TypeError, KeyError, RecursionError):
            issues["row_not_eligible"] += 1
    eligible = [row for row in candidates if len(fingerprints[row[0]]) == 1]
    if not eligible:
        raise PreparationError("no_eligible_request_for_target_rule")
    identity, position, raw, observations, wrapper_created = eligible[0]
    return (
        raw,
        observations,
        {
            "source_row_position_zero_based": position,
            "eligible_distinct_alerts_for_target": len({row[0] for row in eligible}),
            "selection_basis": "first_eligible_source_row_not_model_outcome",
            "inspected_rows": min(len(frame), 200),
            "issue_counts": dict(issues),
            "conflicting_identity_count": sum(
                len(values) > 1 for values in fingerprints.values()
            ),
            "alert_identity_sha256": hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest(),
            "alert_identity_changed": False,
            "alert_data_changed": False,
            "request_wrapper_created": wrapper_created,
            "api_request_schema_validated": True,
            "worker_alert_schema_validated": False,
            "normalized_rule_count": len(observations),
        },
    )


def save(directory, name, raw):
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(
    root,
    *,
    inventory,
    csv_source,
    normalize,
    request_model,
    reviewed_schema_source,
    source_pins,
):
    root = Path(root)
    if any(
        os.environ.get(key, "LOCAL").upper() != "LOCAL"
        for key in ("env_profile", "ENV_PROFILE")
    ):
        raise PreparationError("local_profile_required")
    actual_schema = inventory.bounded_read(
        root / "service/io_models.py", 1024 * 1024
    ).decode("utf-8")
    if not request_schema_matches(actual_schema, reviewed_schema_source):
        raise PreparationError("api_request_schema_changed")
    source = root / "nas/original" / inventory.SAMPLE
    csv_path = Path.home() / "sec-rule-refine-data/rules.csv"
    raw = inventory.bounded_read(source, inventory.MAX_PICKLE)
    if hashlib.sha256(raw).hexdigest() != SAMPLE_SHA:
        raise PreparationError("sample_changed_since_inventory")
    csv_raw = inventory.bounded_read(csv_path, 16 * 1024 * 1024)
    if hashlib.sha256(csv_raw).hexdigest() != CSV_SHA:
        raise PreparationError("reference_csv_changed_since_inventory")
    rules = csv_source(csv_path)
    if rules.file_sha256 != CSV_SHA:
        raise PreparationError("reference_csv_changed_since_inventory")
    rule = rules.get_rule(TARGET[1], topic=TARGET[0])
    import pandas as pd

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        frame = inventory.RestrictedDataFrameUnpickler(io.BytesIO(raw)).load()
    if not isinstance(frame, pd.DataFrame):
        raise PreparationError("dataframe_required")
    request, observations, selected = select(
        frame,
        inventory=inventory,
        rules=rules,
        normalize=normalize,
        request_model=request_model,
    )
    review = source_review(root, inventory, source_pins)
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="ingress-one-ready-", dir=parent))
    result = {
        "check": "rule_refine_prepare_one_alert",
        "status": "prepared_not_submitted",
        "sample_type": "historical_export",
        "source_sha256": SAMPLE_SHA,
        "reference_csv_sha256": CSV_SHA,
        "topic": TARGET[0],
        "rule_code": TARGET[1],
        "rule_config_version": rule["rule_config_version"],
        "request_sha256": hashlib.sha256(request).hexdigest(),
        "request_bytes": len(request),
        "selected": selected,
        "callback_review": review,
        "output_dir": str(directory),
        "business_alerts_submitted": 0,
        "celery_tasks_dispatched": 0,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "application_code_updated": False,
        "automatic_apply": False,
        "actual_ingress_connected": False,
        "normalized_observations_are_preview_only": True,
    }
    save(directory, "request.json", request)
    save(directory, "observation-preview.json", encode(observations))
    save(directory, "selection.json", encode(result))
    return result
