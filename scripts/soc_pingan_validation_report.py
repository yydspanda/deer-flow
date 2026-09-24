#!/usr/bin/env python3
"""Export an offline, read-only SOC second-batch review report using stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

if __package__:
    from .soc_validation_report_data import collect
    from .soc_validation_report_render import build_report, write_report
else:
    from soc_validation_report_data import collect
    from soc_validation_report_render import build_report, write_report


def _progress(message: str) -> None:
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] {message}",
        file=sys.stderr,
        flush=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def enrich_labels(data: dict, index: Path) -> None:
    """Use only the exact frozen JSON catalog, never pickle or a live list cache."""
    metadata = data["metadata"]
    expected = (metadata.get("source_identity") or {}).get("index", {}).get("sha256")
    metadata["label_source"] = {"verified": False, "expected_sha256": expected}
    if not expected or not index.is_file():
        data.setdefault("warnings", []).append(
            "未找到具有冻结哈希的语料 JSON 索引；历史处置标签保留未知。"
        )
        return
    try:
        with index.open("rb") as stream:
            raw = stream.read(128 * 1024 * 1024 + 1)
        if len(raw) > 128 * 1024 * 1024:
            raise ValueError("语料索引超过工具的 128 MiB 读取上限")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError("语料索引与实验冻结的 SHA-256 不一致")
        document = json.loads(raw)
        cases = document.get("cases")
        if not isinstance(cases, list):
            raise ValueError("语料索引缺少 cases")
        by_alert = {}
        for case in cases:
            if (
                not isinstance(case, dict)
                or not isinstance(case.get("alert_id"), str)
                or case["alert_id"] in by_alert
            ):
                raise ValueError("语料索引存在无效或重复告警身份")
            by_alert[case["alert_id"]] = case
        for member in data.get("members", []):
            case = by_alert.get(member["alert_id"], {})
            if (
                not member.get("payload_hash")
                or case.get("payload_hash") != member["payload_hash"]
                or case.get("group_id") != member["group_id"]
            ):
                raise ValueError("语料索引与实验成员的内容哈希或同类组不一致")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        data.setdefault("warnings", []).append(
            f"历史标签未纳入统计：{exc}；其余结果仍按固定轮次统计。"
        )
        return
    for rows_key in ("learning_rows", "validation_rows", "followup_rows"):
        for row in data.get(rows_key, []):
            case = by_alert.get(row["alert_id"], {})
            summary = row.get("summary") or {}
            available = case.get("operational_label_available") is True
            decision_available = summary.get("decision_usable") in (
                True,
                1,
            ) and summary.get("recommended_handling") in {"ignore", "transfer"}
            scorable = (
                available
                and case.get("label_temporal_status") == "valid"
                and case.get("operational_label") in {"忽略", "转交"}
                and decision_available
            )
            row["label"] = {
                "available": available,
                "scorable": scorable,
                "expected_handling": (
                    "ignore" if case["operational_label"] == "忽略" else "transfer"
                )
                if scorable
                else None,
                "temporal_status": case.get("label_temporal_status"),
                "source": "historical_operational_disposition",
                "method": str(case.get("operational_label_method") or "")[:256] or None,
            }
            for key in ("rule_name", "source_type", "category"):
                row[key] = str(case.get(key) or "")[:256] or None
    for member in data.get("members", []):
        for key in ("rule_name", "source_type", "category"):
            member[key] = str(by_alert[member["alert_id"]].get(key) or "")[:256] or None
    metadata["label_source"] = {
        "verified": True,
        "sha256": actual,
        "file_name": index.name,
        "source": "frozen_corpus_json_index",
    }
    # The collector cannot access the corpus file itself; replace its absence
    # notice only after validating the complete immutable member identity.
    data["warnings"] = [
        warning
        for warning in data.get("warnings", [])
        if warning != "历史处置标签不在本次独立数据库提取范围内，不计算处置一致率。"
    ]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="只读导出第二批效果、两批同类组对照及人工抽查表；不调用模型、不更改数据库。"
    )
    result.add_argument(
        "--root",
        type=Path,
        default=Path.home() / "deer-flow",
        help="内网已安装仓库，默认 ~/deer-flow",
    )
    result.add_argument(
        "--database",
        type=Path,
        help="显式指定本地 SQLite 数据库；默认使用仓库的 SOC DEV 数据库",
    )
    result.add_argument("--experiment", help="仅在存在多个实验时指定实验 ID")
    result.add_argument(
        "--round", dest="round_id", help="明确选择第二批全量轮次；默认识别最新全量轮次"
    )
    result.add_argument(
        "--exclude-failed",
        action="store_true",
        help="生成效果汇报版：排除固定轮次中整条运行失败的第二批告警，单列排除范围",
    )
    result.add_argument(
        "--exclude-semantic-failed",
        action="store_true",
        help="排除语义核对状态为 failed 的第二批告警；可与 --exclude-failed 并用，重叠告警只排除一次，保留 partial",
    )
    result.add_argument(
        "--corpus-index",
        type=Path,
        help="可选语料 JSON 索引；仅哈希匹配冻结实验时用于历史处置对照，默认使用仓库已部署的索引",
    )
    result.add_argument(
        "--output-dir", type=Path, help="新的输出目录；已有目录不会覆盖"
    )
    result.add_argument(
        "--groups-per-batch", type=int, default=20, help="人工抽查每批同类组数，默认 20"
    )
    result.add_argument(
        "--query-timeout",
        type=float,
        default=30,
        help="单次数据库查询最长秒数，默认 30，最大 60",
    )
    return result


def _effect_scope(
    data: dict, *, exclude_failed_jobs: bool, exclude_semantic_review_failed: bool
) -> dict:
    """Filter only the fixed validation jobs, after full-scope validation/read."""
    rows = data.get("validation_rows", [])
    included = []
    by_tier = {"main": 0, "supplementary": 0, "unknown": 0}
    by_reason = {"job_failed": 0, "semantic_review_failed": 0}
    overlap_count = 0
    excluded_rows = []
    for row in rows:
        semantic_status = (row.get("semantic_review") or {}).get("status")
        job_failed = exclude_failed_jobs and row["status"] == "failed"
        semantic_failed = exclude_semantic_review_failed and semantic_status == "failed"
        if not (job_failed or semantic_failed):
            included.append(row)
            continue
        by_reason["job_failed"] += int(job_failed)
        by_reason["semantic_review_failed"] += int(semantic_failed)
        overlap_count += int(job_failed and semantic_failed)
        reasons = []
        if job_failed:
            reasons.append("整条任务运行失败")
        if semantic_failed:
            reasons.append("语义核对失败")
        tier = row.get("validation_tier")
        by_tier[tier if tier in by_tier else "unknown"] += 1
        excluded_rows.append(
            {
                **{
                    key: row.get(key)
                    for key in (
                        "alert_id",
                        "validation_tier",
                        "round_id",
                        "job_id",
                        "run_id",
                        "status",
                        "error_code",
                    )
                },
                "semantic_review_status": semantic_status,
                "exclusion_reason": "；".join(reasons),
            }
        )
    return {
        **data,
        "validation_rows": included,
        "metadata": {
            **data.get("metadata", {}),
            "evaluation_scope": {
                "policy": "exclude_failed_validation_stages",
                "exclude_failed_jobs": exclude_failed_jobs,
                "exclude_semantic_review_failed": exclude_semantic_review_failed,
                "original_validation_count": len(rows),
                "included_validation_count": len(included),
                "excluded_validation_count": len(excluded_rows),
                "excluded_by_tier": by_tier,
                "excluded_by_reason": by_reason,
                "excluded_overlap_count": overlap_count,
                "excluded_alerts": excluded_rows,
            },
        },
    }


def run(args: argparse.Namespace) -> Path:
    if not 1 <= args.groups_per_batch <= 100:
        raise ValueError("每批同类组数应为 1 至 100。")
    if not 1 <= args.query_timeout <= 60:
        raise ValueError("单次查询超时应为 1 至 60 秒。")
    root = args.root.expanduser().resolve()
    database = (
        args.database.expanduser()
        if args.database
        else root / "backend/.deer-flow/data/soc_agent_dev.db"
    ).resolve()
    if not database.is_file():
        raise ValueError(
            f"未找到数据库：{database}。请检查 --root；工具不会创建数据库。"
        )
    effect_report = args.exclude_failed or args.exclude_semantic_failed
    prefix = "second-batch-effect-report" if effect_report else "second-batch-report"
    name = f"{prefix}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    output = (
        args.output_dir.expanduser()
        if args.output_dir
        else root / "backend/.deer-flow/soc-internal-validation" / name
    ).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f"输出路径已存在，不会覆盖：{output}")
    _progress(f"只读检查：{database}")
    started = time.monotonic()
    data = collect(
        database,
        experiment_id=args.experiment,
        round_id=args.round_id,
        progress=_progress,
        query_timeout=args.query_timeout,
    )
    index = (
        args.corpus_index.expanduser()
        if args.corpus_index
        else root
        / "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json"
    )
    enrich_labels(data, index)
    if effect_report:
        data = _effect_scope(
            data,
            exclude_failed_jobs=args.exclude_failed,
            exclude_semantic_review_failed=args.exclude_semantic_failed,
        )
        scope = data["metadata"]["evaluation_scope"]
        _progress(
            f"效果范围：原第二批 {scope['original_validation_count']} 条，"
            f"去重排除 {scope['excluded_validation_count']} 条，"
            f"纳入 {scope['included_validation_count']} 条。"
        )
    _progress("数据库读取完成，正在整理同类组、差异与抽查表……")
    report = build_report(data, groups_per_batch=args.groups_per_batch)
    tool_dir = Path(__file__).resolve().parent
    source_files = (
        "soc_pingan_validation_report.py",
        "soc_validation_report_data.py",
        "soc_validation_report_render.py",
    )
    source_hashes = {name: _sha256(tool_dir / name) for name in source_files}
    report.setdefault("metadata", {})["tool_files_sha256"] = source_hashes
    report["metadata"]["exported_at"] = datetime.now(UTC).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".validation-report-", dir=output.parent
    ) as temporary:
        staging = Path(temporary)
        staging.chmod(0o700)
        write_report(report, staging)
        files = sorted(path for path in staging.iterdir() if path.is_file())
        manifest = {
            "schema_version": "soc.validation_effect_report_manifest.v1",
            "generated_at": report["metadata"]["exported_at"],
            "experiment_id": data["metadata"].get("experiment_id"),
            "round_id": data["metadata"].get("round_id"),
            "read_only": True,
            "model_calls": 0,
            "tool_files_sha256": source_hashes,
            "files": {
                path.name: {"sha256": _sha256(path), "size_bytes": path.stat().st_size}
                for path in files
            },
        }
        if effect_report:
            manifest["evaluation_scope"] = {
                key: value
                for key, value in data["metadata"]["evaluation_scope"].items()
                if key != "excluded_alerts"
            }
        with (staging / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        for path in staging.iterdir():
            path.chmod(0o600)
        # Reserve exclusively: even an empty existing report must not be replaced.
        # Publish the completion manifest last; a partial directory has no receipt.
        output.mkdir(mode=0o700)
        for path in sorted(
            staging.iterdir(),
            key=lambda path: (path.name == "manifest.json", path.name),
        ):
            os.replace(path, output / path.name)
    _progress(
        f"完成，用时 {time.monotonic() - started:.1f} 秒；数据库未修改，模型调用 0 次。"
    )
    print(f"报告目录：{output}")
    print(f"先查看：{output / 'REPORT.md'}")
    print(f"人工抽查：{output / 'review.csv'}")
    return output


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        run(args)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"统计已停止：{exc}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        print("统计已取消；数据库未修改。", file=sys.stderr, flush=True)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
