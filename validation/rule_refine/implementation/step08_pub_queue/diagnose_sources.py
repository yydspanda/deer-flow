"""Read-only, value-redacted diagnostics for mismatched queue patch sources."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import stat

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_DISPLAY_LINES = 32
KNOWN_MODELS = frozenset(
    {
        "DeepSeek_V32",
        "DeepSeek_V4_Flash_My",
        "Pub_DeepSeek_V4_Flash",
        "Pingangpt_pro",
        "PingAnGPT_R",
        "DeepSeek_V4_Flash",
        "Deepseek_Qwen_32B_My",
        "qwen3-coder-480b",
    }
)
KNOWN_KEYS = frozenset(
    {
        "model_list",
        "model_name",
        "litellm_params",
        "model_info",
        "model",
        "api_base",
        "api_key",
        "api_version",
        "headers",
        "extra_headers",
        "master_key",
        "general_settings",
        "litellm_settings",
        "router_settings",
        "drop_params",
        "set_verbose",
        "max_chat_completion_tokens",
        "max_input_tokens",
        "max_output_tokens",
        "max_tokens",
        "timeout",
        "request_timeout",
        "num_retries",
        "max_retries",
        "rpm",
        "tpm",
        "fallbacks",
        "context_window_fallbacks",
    }
)
BOOL_KEYS = frozenset({"drop_params", "set_verbose"})
INT_KEYS = frozenset(
    {
        "max_chat_completion_tokens",
        "max_input_tokens",
        "max_output_tokens",
        "max_tokens",
        "timeout",
        "request_timeout",
        "num_retries",
        "max_retries",
        "rpm",
        "tpm",
    }
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def line_summary(line):
    """No arbitrary scalar, comment, key, address or source text leaves this function."""
    result = {"sha256": digest(line.encode("utf-8"))}
    if not line.strip():
        result["kind"] = "blank"
        return result
    if line.lstrip().startswith("#"):
        result["kind"] = "comment_content_hidden"
        return result
    match = re.fullmatch(r"( *)(- +)?([A-Za-z_][A-Za-z0-9_]*):(?: +(.*)| *)", line)
    if match is None:
        result["kind"] = "content_hidden"
        return result
    key, value = match[3], match[4] or ""
    result.update(
        {
            "kind": "mapping_line",
            "indent": len(match[1]),
            "list_item": bool(match[2]),
            "key": key if key in KNOWN_KEYS else "unknown_key_hidden",
        }
    )
    # Literal allowlists, not generic YAML parsing or heuristic secret replacement.
    scalar = value.split(" #", 1)[0].strip()
    if key == "model_name":
        if len(scalar) >= 2 and scalar[0] == scalar[-1] and scalar[0] in "\"'":
            scalar = scalar[1:-1]
        result["model_alias"] = (
            scalar if scalar in KNOWN_MODELS else "unknown_alias_hidden"
        )
    elif key in BOOL_KEYS and scalar in {"true", "false"}:
        result["setting_value"] = scalar == "true"
    elif key in INT_KEYS and re.fullmatch(r"[0-9]{1,9}", scalar):
        # This is a line-shape view, not a parser. A settings-looking line may
        # belong to headers or a block scalar containing a numeric credential.
        result["value"] = "integer_hidden"
    elif value:
        result["value"] = "hidden"
    else:
        result["value"] = "empty_or_nested"
    return result


def yaml_reference(data):
    text = data.decode("utf-8")
    return {
        "sha256": digest(data),
        "lines": [line_summary(line) for line in text.splitlines()],
    }


def yaml_difference(data, reference):
    text = data.decode("utf-8")
    current = [line_summary(line) for line in text.splitlines()]
    previous = reference["lines"]
    matcher = difflib.SequenceMatcher(
        a=[row["sha256"] for row in previous],
        b=[row["sha256"] for row in current],
        autojunk=False,
    )
    differences = []
    remaining = MAX_DISPLAY_LINES
    for kind, start, end, new_start, new_end in matcher.get_opcodes():
        if kind == "equal":
            continue
        old_rows = previous[start:end][:remaining]
        remaining -= len(old_rows)
        new_rows = current[new_start:new_end][:remaining]
        remaining -= len(new_rows)
        differences.append(
            {
                "kind": kind,
                "reviewed_line_start": start + 1,
                "reviewed_line_count": end - start,
                "current_line_start": new_start + 1,
                "current_line_count": new_end - new_start,
                "reviewed_lines": old_rows,
                "current_lines": new_rows,
                "details_truncated": len(old_rows) != end - start
                or len(new_rows) != new_end - new_start,
            }
        )
    aliases = [
        {"line": index + 1, "model_alias": row["model_alias"]}
        for index, row in enumerate(current)
        if "model_alias" in row
    ]
    return {
        "line_count": len(current),
        "has_utf8_bom": data.startswith(b"\xef\xbb\xbf"),
        "crlf_count": data.count(b"\r\n"),
        "lf_count": data.count(b"\n"),
        "normalized_lf_no_bom_sha256": digest(
            text.removeprefix("\ufeff").replace("\r\n", "\n").encode("utf-8")
        ),
        "comparison_reference_sha256": reference["sha256"],
        "known_model_aliases": aliases[:32],
        "alias_list_truncated": len(aliases) > 32,
        "differences": differences[:32],
        "difference_list_truncated": len(differences) > 32,
        "analysis_scope": "line_shapes_and_allowlisted_literals_not_yaml_semantics",
    }


def read_source(root, name):
    path = root / name
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink():
            return None, "symlink_not_read"
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            return None, "non_regular_or_linked_file_not_read"
        if metadata.st_size > MAX_FILE_BYTES:
            return None, "file_too_large_not_read"
        return path.read_bytes(), None
    except OSError:
        return None, "file_not_readable"


def diagnose(root, reference):
    root = Path(root).expanduser().resolve(strict=True)
    rows = []
    for name, pairs in sorted(reference["pins"].items()):
        row = {"file": name}
        data, error = read_source(root, name)
        if error:
            row["status"] = error
        else:
            current = digest(data)
            row.update({"sha256": current, "bytes": len(data)})
            row["status"] = (
                "reviewed_before"
                if current in pairs
                else "reviewed_after"
                if current in pairs.values()
                else "unreviewed_source"
            )
            if row["status"] == "unreviewed_source" and name in reference["yaml"]:
                try:
                    row["redacted_difference"] = yaml_difference(
                        data, reference["yaml"][name]
                    )
                except UnicodeError:
                    row["redacted_difference"] = {"status": "not_utf8"}
        rows.append(row)
    unknown = [
        row["file"]
        for row in rows
        if row["status"] not in {"reviewed_before", "reviewed_after"}
    ]
    return {
        "check": "sec_know_pub_queue_source_diagnosis",
        "status": "differences_found" if unknown else "all_files_recognized",
        "read_only": True,
        "files_checked": len(rows),
        "unrecognized_files": unknown,
        "files": rows,
        "application_code_updated": False,
        "files_written": 0,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "services_started": False,
        "automatic_restart": False,
        "installation_allowed_by_this_diagnosis": False,
    }


def main(reference):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path.home() / "sec_know_model"
    )
    args = parser.parse_args()
    try:
        result = diagnose(args.project_root, reference)
    except (OSError, ValueError):
        result = {
            "check": "sec_know_pub_queue_source_diagnosis",
            "status": "diagnosis_failed",
            "error_code": "source_directory_or_reference_unavailable",
            "read_only": True,
            "files_written": 0,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "diagnosis_failed" else 0
