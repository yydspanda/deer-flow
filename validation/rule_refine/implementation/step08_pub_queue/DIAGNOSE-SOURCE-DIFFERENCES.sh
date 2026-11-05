bash <<'SOC_PUB_QUEUE_DIAG'
set -euo pipefail
queue_project="${SEC_KNOW_PROJECT_ROOT:-$HOME/sec_know_model}"
queue_python="$queue_project/.venv/bin/python"
if [ ! -x "$queue_python" ]; then queue_python=python3; fi
echo '只读核对全部队列补丁文件；差异输出隐藏地址、凭据及未知字符串，不修改源码或启动服务。'
"$queue_python" -B - --project-root "$queue_project" <<'SOC_PUB_QUEUE_DIAG_PY'
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

import base64, zlib
_DIAGNOSTIC_REFERENCE = json.loads(zlib.decompress(base64.b64decode("""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
""")))
raise SystemExit(main(_DIAGNOSTIC_REFERENCE))
SOC_PUB_QUEUE_DIAG_PY
SOC_PUB_QUEUE_DIAG
