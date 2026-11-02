"""Inspect the named historical sample; no application imports or submissions."""

import ast
import csv
import hashlib
import io
import json
import pickle
import warnings
from collections import Counter, defaultdict
from pathlib import Path

SAMPLE = "full_alert_2026_month_forth_sample_200.pkl"
FILENAMES = (
    SAMPLE,
    "所有的预警数据_采样.pkl",
    "所有的预警数据.pkl",
    "full_alert_2026_month_forth.pkl",
)
INJECTOR_SHA = "3361f29d5170e9408e0494b2e266c0e3c4bb41b349023e0c7387584dc6b8c58d"
MAX_PICKLE = 64 * 1024 * 1024
ALLOWED_PICKLE_GLOBALS = {
    ("pandas", "DataFrame"),
    ("pandas", "Index"),
    ("pandas", "RangeIndex"),
    ("pandas.core.frame", "DataFrame"),
    ("pandas.core.internals.managers", "BlockManager"),
    ("pandas._libs.internals", "_unpickle_block"),
    ("numpy.core.numeric", "_frombuffer"),
    ("numpy._core.numeric", "_frombuffer"),
    ("numpy", "dtype"),
    ("builtins", "slice"),
    ("numpy.core.multiarray", "_reconstruct"),
    ("numpy._core.multiarray", "_reconstruct"),
    ("numpy", "ndarray"),
    ("collections", "Counter"),
    ("pandas.core.indexes.base", "_new_Index"),
    ("pandas.core.indexes.base", "Index"),
    ("pandas.core.indexes.range", "RangeIndex"),
}


class RestrictedDataFrameUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) not in ALLOWED_PICKLE_GLOBALS:
            raise pickle.UnpicklingError("unsupported_pickle_global")
        return super().find_class(module, name)


def bounded_read(path, limit):
    if path.stat().st_size > limit:
        raise ValueError("file_too_large")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("file_too_large")
    return raw


def injector_info(root):
    try:
        raw = bounded_read(root / "validation/inject_priority9_tasks.py", 256 * 1024)
        tree = ast.parse(raw.decode("utf-8"))
        nodes = list(ast.walk(tree))
        defaults = {}
        for node in nodes:
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
            ):
                continue
            flags = [
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]
            default = next(
                (
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "default" and isinstance(kw.value, ast.Constant)
                ),
                None,
            )
            if "--count" in flags and type(default) is int:
                defaults["count"] = default
            if "--dry-run" in flags:
                defaults["dry_run_option_present"] = True
        return {
            "status": "inspected",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "matches_source_zip": hashlib.sha256(raw).hexdigest() == INJECTOR_SHA,
            "defaults": defaults,
            "inline_fixture_present": any(
                isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "BASE_ALERT_DATA"
                    for t in n.targets
                )
                for n in nodes
            ),
            "random_id_call_present": any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "random"
                and n.func.attr == "randint"
                for n in nodes
            ),
            "script_executed": False,
        }
    except FileNotFoundError:
        return {"status": "not_found", "script_executed": False}
    except (OSError, ValueError, SyntaxError, RecursionError) as error:
        return {
            "status": "inspection_failed",
            "error_type": type(error).__name__,
            "script_executed": False,
        }


def csv_reference(path):
    try:
        raw = bounded_read(path, 16 * 1024 * 1024)
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        if not {"topic", "rule_code"}.issubset(reader.fieldnames or []):
            raise ValueError("unexpected_csv_columns")
        scopes = Counter()
        for row in reader:
            if None in row or not row.get("topic") or not row.get("rule_code"):
                raise ValueError("invalid_csv_row")
            scopes[(row["topic"], row["rule_code"])] += 1
        return {
            "status": "read",
            "records": sum(scopes.values()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "running_process_source_verified": False,
        }, scopes
    except (OSError, ValueError, csv.Error) as error:
        return {"status": "unavailable", "error_type": type(error).__name__}, None


def text_id(value):
    if type(value) is int and value >= 0:
        value = str(value)
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 512
        and value == value.strip()
        and value.isprintable()
    ):
        return value
    return None


def bounded_json_shape(value):
    budget, nodes = 2 * 1024 * 1024, 20000

    def visit(item, depth=0):
        nonlocal budget, nodes
        nodes -= 1
        if nodes < 0 or depth > 32:
            raise ValueError("row_over_budget")
        if isinstance(item, str):
            if len(item) > budget:
                raise ValueError("row_over_budget")
            budget -= len(item.encode("utf-8"))
        elif item is None or type(item) in (bool, int, float):
            budget -= 32
        elif isinstance(item, (dict, list)):
            if len(item) > nodes:
                raise ValueError("row_over_budget")
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError("row_not_json")
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
        else:
            raise ValueError("row_not_json")
        if budget < 0:
            raise ValueError("row_over_budget")

    visit(value)


def inspect_frame(frame, scopes):
    result = {
        "status": "inspected",
        "total_rows": len(frame),
        "column_count": len(frame.columns),
        "known_columns": [
            name
            for name in ("alert_id", "alert_full_data", "agent_response", "alert_data")
            if name in frame.columns
        ],
        "inspected_rows": 0,
        "raw_values_exported": False,
        "alert_ids_exported": False,
    }
    if not frame.columns.is_unique or "alert_full_data" not in frame.columns:
        return {**result, "status": "unrecognized_columns"}
    seen, usable, by_scope, issues = set(), set(), defaultdict(set), Counter()
    duplicate_rows = 0
    for value in frame["alert_full_data"].iloc[:200]:
        result["inspected_rows"] += 1
        if isinstance(value, str):
            if len(value.encode("utf-8")) > 2 * 1024 * 1024:
                issues["row_over_budget"] += 1
                continue
            try:
                value = json.loads(value)
            except (ValueError, RecursionError):
                issues["invalid_json"] += 1
                continue
        if not isinstance(value, dict):
            issues["not_object"] += 1
            continue
        try:
            bounded_json_shape(value)
        except (ValueError, RecursionError):
            issues["row_not_bounded_json"] += 1
            continue
        payload = value.get("alert_data", value)
        alert = payload.get("alert") if isinstance(payload, dict) else None
        identity = text_id(alert.get("alertId")) if isinstance(alert, dict) else None
        if identity is None:
            issues["missing_or_invalid_identity"] += 1
            continue
        if (
            "alert_data" in value
            and "alert_id" in value
            and text_id(value["alert_id"]) != identity
        ):
            issues["identity_mismatch"] += 1
            continue
        duplicate_rows += int(identity in seen)
        seen.add(identity)
        hits = alert.get("hitLog")
        if not isinstance(hits, list):
            issues["invalid_hit_logs"] += 1
            continue
        pairs = set()
        for hit in hits[:100]:
            if not isinstance(hit, dict):
                continue
            topic, rule = text_id(hit.get("topic")), text_id(hit.get("ruleCode"))
            logs = hit.get("zeusRawLogs")
            if isinstance(logs, list) and len(logs) > 100:
                issues["raw_logs_truncated"] += 1
            if (
                topic
                and rule
                and isinstance(logs, list)
                and any(isinstance(log, dict) and log for log in logs[:100])
            ):
                pairs.add((topic, rule))
        if len(hits) > 100:
            issues["hit_logs_truncated"] += 1
        if not pairs:
            issues["no_rule_with_raw_log"] += 1
            continue
        usable.add(identity)
        for pair in pairs:
            by_scope[pair].add(identity)
    top = []
    for (topic, rule), identities in sorted(
        by_scope.items(), key=lambda item: (-len(item[1]), item[0])
    )[:10]:
        match_count = None if scopes is None else scopes[(topic, rule)]
        top.append(
            {
                "topic": topic,
                "rule_code": rule,
                "unique_alerts": len(identities),
                "csv_config_matches": match_count,
                "csv_unique_match": None if match_count is None else match_count == 1,
            }
        )
    return {
        **result,
        "unique_alert_ids": len(seen),
        "duplicate_identity_rows": duplicate_rows,
        "usable_unique_alerts": len(usable),
        "topic_rule_pairs": len(by_scope),
        "top_10_rules": top,
        "issue_counts": dict(issues),
        "inspected_scope": "first_at_most_200_rows",
        "full_request_schema_verified": False,
        "duplicate_payload_equivalence_verified": False,
    }


def sample_info(path, scopes):
    try:
        if path.stat().st_size > MAX_PICKLE:
            return {"status": "sample_too_large_not_loaded", "max_bytes": MAX_PICKLE}
        raw = bounded_read(path, MAX_PICKLE)
        source = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "restricted_loader": True,
        }
        import pandas as pd

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            frame = RestrictedDataFrameUnpickler(io.BytesIO(raw)).load()
        if not isinstance(frame, pd.DataFrame):
            return {**source, "status": "not_a_dataframe"}
        return {**source, **inspect_frame(frame, scopes)}
    except FileNotFoundError:
        return {"status": "sample_not_found"}
    except Exception as error:
        return {
            "status": "sample_inspection_failed",
            "error_type": type(error).__name__,
            "restricted_loader": True,
        }


def check_materials(root, data_dir=None):
    root = Path(root)
    directory = root / "nas/original" if data_dir is None else Path(data_dir)
    files = []
    for name in FILENAMES:
        row = {"name": name, "contents_requested": name == SAMPLE}
        try:
            row.update(exists=True, bytes=(directory / name).stat().st_size)
        except OSError:
            row.update(exists=False)
        files.append(row)
    reference, scopes = csv_reference(Path.home() / "sec-rule-refine-data/rules.csv")
    return {
        "check": "rule_refine_historical_materials",
        "read_only": True,
        "sample_type": "historical_export",
        "current_alert_rate_measured": False,
        "volume_signal_created": False,
        "model_calls": 0,
        "business_alerts_submitted": 0,
        "files": files,
        "injector": injector_info(root),
        "reference_csv": reference,
        "sample": sample_info(directory / SAMPLE, scopes),
    }
