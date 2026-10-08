#!/usr/bin/env python3
"""Collect one Memory's frozen-snapshot evidence, using SQLite and stdlib only.

This is an evidence exporter, not another eligibility policy or history repair.
No application imports, provider calls, migrations, or business writes occur.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

MAX_ROWS = 2000
AUDIT_FIELDS = (
    "action",
    "decision",
    "previous_record_version",
    "result_record_version",
    "previous_retrieval_enabled",
    "retrieval_enabled",
    "policy_version",
    "retrieval_valid_until",
    "retrieval_review_due_at",
    "previous_record_valid_from",
    "previous_record_valid_until",
    "record_valid_from",
    "record_valid_until",
    "memory_id",
    "memory_version",
)


def _time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=UTC)
            if parsed.tzinfo is None
            else parsed.astimezone(UTC)
        )
    except ValueError:
        return None


def _as_of(row, cutoff):
    if row is None:
        return {"status": "unknown_record_not_available"}
    created, updated = _time(row.get("created_at")), _time(row.get("updated_at"))
    if created is not None and created > cutoff:
        return {
            "status": "created_after_round_time",
            "actual_snapshot_state": "unknown",
        }
    if created is not None and updated is not None and created <= updated <= cutoff:
        return {
            "status": "current_record_timestamps_precede_round_time",
            "evidence": "current.created_at/updated_at <= round.created_at",
            "actual_snapshot_state": "unknown",
        }
    return {"status": "unknown_current_record_changed"}


class Reader:
    def __init__(self, conn, timeout):
        self.conn, self.timeout = conn, timeout

    def read(self, sql, params=()):
        deadline = time.monotonic() + self.timeout
        self.conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            with closing(self.conn.execute(sql, params)) as cursor:
                rows = cursor.fetchmany(MAX_ROWS + 1)
            if len(rows) > MAX_ROWS:
                raise ValueError("取证记录超过 2000 项边界；不输出截断证据")
            return [dict(row) for row in rows]
        finally:
            self.conn.set_progress_handler(None, 0)


def _one(reader, sql, params):
    rows = reader.read(sql, params)
    if len(rows) > 1:
        raise ValueError("目标身份不唯一")
    return rows[0] if rows else None


def _decode(row, keys):
    if row is not None:
        for key in keys:
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
    return row


def collect(
    database: Path,
    *,
    round_id: str,
    memory_id: str,
    candidate_id: str | None = None,
    query_timeout: float = 30,
):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError("未找到数据库；不会创建数据库")
    if not 0 < query_timeout <= 60:
        raise ValueError("query-timeout 必须在 0 到 60 秒之间")
    with closing(
        sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2
        )
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        reader = Reader(conn, query_timeout)
        version = conn.execute("PRAGMA data_version").fetchone()[0]
        tables = {
            row["name"]
            for row in reader.read("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {
            "soc_corpus_rounds",
            "soc_corpus_experiments",
            "soc_memory_records",
            "soc_memory_candidates",
        }
        if missing := required - tables:
            raise ValueError("数据库缺少取证表：" + ", ".join(sorted(missing)))
        round_ = _one(
            reader,
            """
            SELECT round_id,experiment_id,batch,created_at,
              json_extract(record_payload,'$.memory_mode') AS memory_mode,
              json_type(record_payload,'$.memory_snapshot') AS memory_snapshot_type,
              json_extract(record_payload,'$.memory_snapshot') AS memory_snapshot
            FROM soc_corpus_rounds WHERE round_id=?
        """,
            (round_id,),
        )
        if round_ is None:
            raise ValueError("指定固定轮次不在此数据库；不回退其他轮次")
        cutoff = _time(round_["created_at"])
        if (
            cutoff is None
            or round_["batch"] != "validation"
            or round_["memory_mode"] != "snapshot"
        ):
            raise ValueError("指定轮次不是具有可解析创建时间的第二批快照")
        snapshot_raw = round_.pop("memory_snapshot")
        if round_.pop("memory_snapshot_type") != "array":
            raise ValueError("冻结快照字段缺失或不是数组；不能认定为空快照")
        snapshot = json.loads(snapshot_raw)
        if not isinstance(snapshot, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("memory_id"), str)
            or not item["memory_id"].strip()
            or type(item.get("version")) is not int
            or item["version"] <= 0
            for item in snapshot
        ):
            raise ValueError("冻结快照格式无效")
        experiment = _one(
            reader,
            """
            SELECT experiment_id,
              json_extract(record_payload,'$.tenant_id') AS tenant_id,
              json_extract(record_payload,'$.environment') AS environment,
              json_extract(record_payload,'$.source_identity.memory_profile') AS memory_profile
            FROM soc_corpus_experiments WHERE experiment_id=?
        """,
            (round_["experiment_id"],),
        )
        _decode(experiment, ("memory_profile",))
        record = _one(
            reader,
            """
            SELECT memory_id,source_candidate_id,created_at,updated_at,
              json_extract(record_payload,'$.version') AS version,
              json_extract(record_payload,'$.status') AS status,
              json_extract(record_payload,'$.tenant_id') AS tenant_id,
              json_extract(record_payload,'$.tenant_scope') AS tenant_scope,
              json_extract(record_payload,'$.retrieval_enabled') AS retrieval_enabled,
              json_object('valid_from',json_extract(record_payload,'$.validity.valid_from'),
                          'valid_until',json_extract(record_payload,'$.validity.valid_until'),
                          'review_after_days',json_extract(record_payload,'$.validity.review_after_days')) AS validity,
              json_extract(record_payload,'$.retrieval_valid_until') AS retrieval_valid_until,
              json_extract(record_payload,'$.retrieval_review_due_at') AS retrieval_review_due_at,
              json_extract(record_payload,'$.retrieval_updated_at') AS retrieval_updated_at,
              json_extract(record_payload,'$.retrieval_policy_version') AS retrieval_policy_version,
              json_extract(record_payload,'$.facets.environment') AS environment,
              json_extract(record_payload,'$.applicability.profile_id') AS profile_id,
              json_extract(record_payload,'$.applicability.profile_version') AS profile_version,
              json_extract(record_payload,'$.applicability.feature_schema_version') AS feature_schema_version
            FROM soc_memory_records WHERE memory_id=?
        """,
            (memory_id,),
        )
        _decode(record, ("validity", "environment"))
        if record:
            if candidate_id and candidate_id != record["source_candidate_id"]:
                raise ValueError("指定候选与经验来源候选不一致")
            candidate_id = record["source_candidate_id"]
            if record["retrieval_enabled"] is not None:
                record["retrieval_enabled"] = bool(record["retrieval_enabled"])
        candidate = (
            _one(
                reader,
                """
            SELECT candidate_id,source_run_id,created_at,updated_at,
              json_extract(candidate_payload,'$.status') AS status,
              json_extract(candidate_payload,'$.reviewed_at') AS reviewed_at,
              json_extract(candidate_payload,'$.metadata.experiment_id') AS metadata_experiment_id,
              json_extract(candidate_payload,'$.source.metadata.experiment_id') AS source_metadata_experiment_id
            FROM soc_memory_candidates WHERE candidate_id=?
        """,
                (candidate_id,),
            )
            if candidate_id
            else None
        )
        gaps = []
        links = []
        link_tables = {"soc_corpus_round_items", "soc_processing_jobs"}
        if link_tables <= tables and candidate and candidate["source_run_id"]:
            links = reader.read(
                """
                SELECT r.round_id,r.experiment_id,r.batch,r.created_at,i.alert_id,j.run_id
                FROM soc_processing_jobs j
                JOIN soc_corpus_round_items i ON i.job_id=j.job_id
                JOIN soc_corpus_rounds r ON r.round_id=i.round_id
                WHERE j.run_id=? AND r.experiment_id=? AND r.batch='learning'
                ORDER BY r.created_at,r.round_id,i.alert_id
            """,
                (candidate["source_run_id"], round_["experiment_id"]),
            )
        elif not link_tables <= tables:
            gaps.append("learning_source_run_tables_unavailable")
        revisions = []
        if "soc_memory_revision_proposals" in tables:
            revisions = reader.read(
                """
                SELECT proposal_id,memory_id,created_at,
                  json_extract(proposal_payload,'$.status') AS status,
                  json_extract(proposal_payload,'$.reviewed_at') AS reviewed_at,
                  json_extract(proposal_payload,'$.memory_version') AS memory_version
                FROM soc_memory_revision_proposals WHERE memory_id=? ORDER BY created_at,proposal_id
            """,
                (memory_id,),
            )
            for item in revisions:
                item["temporal_scope"] = "current_record"
        else:
            gaps.append("revision_table_unavailable")
        audits = []
        if "soc_mutation_audit_log" in tables:
            projection = ",".join(
                f"json_extract(record_payload,'$.payload.{key}') AS {key}"
                for key in AUDIT_FIELDS
            )
            audits = reader.read(
                """
                SELECT audit_id,target_id,result_ref,occurred_at,
                  json_extract(record_payload,'$.operation') AS operation,
                  json_extract(record_payload,'$.result_status') AS result_status,
            """
                + projection
                + """
                FROM soc_mutation_audit_log
                WHERE target_id IN (SELECT value FROM json_each(?)) OR result_ref IN (?,?)
                ORDER BY occurred_at,audit_id
            """,
                (
                    json.dumps(
                        [memory_id, candidate_id, round_id]
                        + [item["proposal_id"] for item in revisions]
                    ),
                    memory_id,
                    candidate_id,
                ),
            )
            for item in audits:
                at = _time(item["occurred_at"])
                item["relative_to_round_created_at"] = (
                    "unknown"
                    if at is None
                    else "before_or_at"
                    if at <= cutoff
                    else "after"
                )
                item["payload"] = {
                    key: item.pop(key)
                    for key in AUDIT_FIELDS
                    if item.get(key) is not None
                }
                for key in ("previous_retrieval_enabled", "retrieval_enabled"):
                    if key in item["payload"]:
                        item["payload"][key] = bool(item["payload"][key])
                for key in AUDIT_FIELDS:
                    item.pop(key, None)
        else:
            gaps.append("mutation_audit_table_unavailable")
        if version != conn.execute("PRAGMA data_version").fetchone()[0]:
            raise ValueError("读取期间数据库发生变化；不输出混合时间的取证结果")
        if record is None:
            gaps.append("memory_record_unavailable")
        if candidate is None:
            gaps.append("candidate_record_unavailable")
        return {
            "schema_version": "soc.memory_snapshot_audit.v1",
            "collected_at": datetime.now(UTC).isoformat(),
            "read_only": True,
            "model_calls": 0,
            "round": round_,
            "experiment": experiment,
            "snapshot": {
                "entry_count": len(snapshot),
                "target_entries": [
                    {
                        key: item.get(key)
                        for key in (
                            "memory_id",
                            "version",
                            "content_hash",
                            "facets_hash",
                            "record_hash",
                        )
                    }
                    for item in snapshot
                    if item.get("memory_id") == memory_id
                ],
                "complete_snapshot_sha256": hashlib.sha256(
                    json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
            },
            "memory": {
                "requested_id": memory_id,
                "current": record,
                "as_of": _as_of(record, cutoff),
            },
            "candidate": {
                "requested_id": candidate_id,
                "current": candidate,
                "as_of": _as_of(candidate, cutoff),
            },
            "learning_scope_current": {
                "source_run_links": links,
                "candidate_metadata_matches": bool(
                    candidate
                    and candidate["metadata_experiment_id"] == round_["experiment_id"]
                ),
                "source_metadata_matches": bool(
                    candidate
                    and candidate["source_metadata_experiment_id"]
                    == round_["experiment_id"]
                ),
                "historical_link_state": "not_reconstructed_from_current_links",
            },
            "audits": audits,
            "revision_proposals": revisions,
            "evidence_gaps": gaps,
            "historical_exclusion_reason": "not_inferred",
            "boundaries": [
                "Record timestamp comparisons are evidence relative to round.created_at, not proof of state at snapshot selection.",
                "Snapshot selection precedes round.created_at; clock rollback handling can advance that timestamp beyond physical creation time.",
                "Audit events are retained individually; missing events do not prove no activation occurred.",
                "Group lineage through observations differs from the snapshot selector's source-run/experiment branches.",
                "Environment and Profile are exported for comparison, not assumed to be snapshot admission gates.",
                "No eligibility policy is reimplemented and no records are activated or rewritten.",
                "SQLite timestamps without an offset follow the application's UTC storage convention.",
            ],
        }


def selector_sources(root):
    """Read the installed selector definitions without importing or executing them."""
    definitions = {
        "backend/soc_agent/demo/corpus_experiments.py": {"create_round", "_eligible"},
        "backend/soc_agent/db/corpus_experiments.py": {
            "_learning_candidate_scope",
            "learning_memory_records",
        },
    }
    result = []
    for name, functions in definitions.items():
        path = root / name
        if not path.is_file():
            result.append({"path": name, "status": "unavailable"})
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        selected = []
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.FunctionDef) and node.name in functions:
                source = ast.get_source_segment(text, node)
                selected.append(
                    {
                        "function": node.name,
                        "line": node.lineno,
                        "sha256": hashlib.sha256(source.encode()).hexdigest(),
                        "source": source,
                    }
                )
        missing = sorted(functions - {item["function"] for item in selected})
        result.append(
            {
                "path": name,
                "status": "partial" if missing else "available",
                "missing_definitions": missing,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "definitions": selected,
                "temporal_scope": "installed_code_at_collection_not_proof_of_freeze_time_code",
            }
        )
    return result


def _installed_revision(root):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        value = result.stdout.strip()
        return (
            value
            if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", value)
            else None
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="只读核对单条经验与固定轮次快照；不调用模型、不修改业务库。"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "deer-flow")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--round", dest="round_id", required=True)
    parser.add_argument("--memory", dest="memory_id", required=True)
    parser.add_argument("--candidate", dest="candidate_id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--query-timeout", type=float, default=30)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    database = args.database or root / "backend/.deer-flow/data/soc_agent_dev.db"
    output = args.output_dir or root / "backend/.deer-flow/soc-internal-validation" / (
        "memory-snapshot-audit-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid4().hex[:8]
    )
    try:
        if output.exists() or output.is_symlink():
            raise ValueError("输出目录已存在；不会覆盖历史取证")
        evidence = collect(
            database,
            round_id=args.round_id,
            memory_id=args.memory_id,
            candidate_id=args.candidate_id,
            query_timeout=args.query_timeout,
        )
        evidence["selector_sources"] = selector_sources(root)
        evidence["installed_git_revision"] = _installed_revision(root)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".memory-snapshot-", dir=output.parent
        ) as temporary:
            staging = Path(temporary)
            payload = (
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
            ).encode()
            (staging / "evidence.json").write_bytes(payload)
            manifest = {
                "schema_version": "soc.memory_snapshot_audit_manifest.v1",
                "read_only": True,
                "model_calls": 0,
                "round_id": args.round_id,
                "memory_id": args.memory_id,
                "evidence_sha256": hashlib.sha256(payload).hexdigest(),
                "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for path in staging.iterdir():
                path.chmod(0o600)
            output.mkdir(mode=0o700)
            for name in ("evidence.json", "manifest.json"):
                os.replace(staging / name, output / name)
        print(f"只读取证完成：{output}；数据库未修改，模型调用 0 次。")
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"取证已停止：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
