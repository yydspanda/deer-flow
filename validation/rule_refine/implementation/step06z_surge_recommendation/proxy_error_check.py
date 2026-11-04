"""Read local proxy error lines near a saved run; never import the application."""

import json
import os
import re
from datetime import datetime
from pathlib import Path

ERROR = re.compile(
    r"BadRequestError|400 Bad Request|context.{0,30}(length|limit)|maximum.{0,30}tokens|"
    r"too many tokens|request.{0,20}too large|unsupported.{0,20}(param|argument)|"
    r"invalid_request|upstream error|upstream transient error|\"upstream\"",
    re.I,
)
STAMP = re.compile(r"(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def clean(line):
    line = ANSI.sub("", line)
    # Suppress echoed input/output payloads; preserve the preceding error message.
    line = re.split(
        r"""["'](?:messages|prompt|alert_data|raw_logs|content)["']\s*:\s*""",
        line,
        maxsplit=1,
    )[0]
    line = re.sub(
        r"(?i)\b(?:bearer|basic)\s+[a-z0-9._~+/=-]+", "Bearer [redacted]", line
    )
    line = re.sub(r"\bsk-[a-zA-Z0-9_-]+", "[redacted-key]", line)
    line = re.sub(
        r"""(?i)(["']?(?:api[_-]?key|authorization|access_token|password|secret|token)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,}]+)""",
        r"\1[redacted]",
        line,
    )
    line = re.sub(r"https?://[^\s<>\"']+", "[url omitted]", line)
    return line[:1800]


def inspect(root, report, log_directory):
    result = {
        "check": "rule_refine_proxy_error_logs",
        "read_only": True,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "report_dir": str(report),
        "log_directory": str(log_directory),
        "attribution": "near_run_time_not_exclusive_request_identity",
        "files": [],
    }
    start, end = None, None
    marker, saved = (
        report / "generate/request-started.json",
        report / "generate/result.json",
    )
    if marker.is_file():
        start = marker.stat().st_mtime - 120
        end = (
            saved.stat().st_mtime if saved.is_file() else marker.stat().st_mtime + 600
        ) + 120
        result["time_window_local"] = [
            datetime.fromtimestamp(t).isoformat(timespec="seconds")
            for t in (start, end)
        ]
    paths = sorted(
        [
            p
            for name in ("litellm.log*", "openai_completion.log*")
            for p in log_directory.glob(name)
            if p.is_file() and not p.name.endswith(".gz")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:16]
    for path in paths:
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.seek(max(0, size - 4 * 1024 * 1024))
            raw = stream.read()
        matches, last_time = [], None
        for line in raw.decode("utf-8", errors="replace").splitlines():
            stamp = STAMP.search(ANSI.sub("", line))
            if stamp:
                try:
                    last_time = datetime.fromisoformat(
                        " ".join(stamp.groups())
                    ).timestamp()
                except ValueError:
                    last_time = None
            if (
                start is not None
                and last_time is not None
                and not start <= last_time <= end
            ):
                continue
            if ERROR.search(line):
                excerpt = clean(line)
                if excerpt:
                    matches.append(
                        {
                            "near_saved_run_time": last_time is not None
                            and start is not None,
                            "excerpt": excerpt,
                        }
                    )
        result["files"].append(
            {
                "file": str(path),
                "tail_bytes_read": len(raw),
                "matching_lines_in_tail": len(matches),
                "matches": matches[-8:],
            }
        )
    result["status"] = (
        "error_lines_found"
        if any(f["matches"] for f in result["files"])
        else "no_error_detail_found"
    )
    result["saved_provider_error_exists"] = (
        report / "generate/provider-error.json"
    ).is_file()
    return result


def main():
    root = Path.home() / "sec_know_model"
    reports = list(
        (Path.home() / "sec-rule-refine-data").glob("surge-ai-review-*/summary.json")
    )
    default = max(reports, key=lambda p: p.stat().st_mtime).parent if reports else None
    print(f"失败报告目录（回车用 {default}）：", end="", flush=True)
    with open("/dev/tty", "r", encoding="utf-8") as terminal:
        entered = terminal.readline().strip()
    if not entered and default is None:
        raise SystemExit("未找到报告，请填写已有结果目录。")
    report = Path(entered).expanduser() if entered else default
    if not (report / "summary.json").is_file():
        raise SystemExit("报告目录没有summary.json；未读取其他材料。")
    logs = Path(os.environ.get("PROXY_RUN_DIR", str(root / "nas/log"))).expanduser()
    print(json.dumps(inspect(root, report, logs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
