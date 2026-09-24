"""Bounded, read-only SQLite extraction for the offline validation report.

This module deliberately imports neither application code nor third-party packages.
Every SELECT finishes before Python processes its page. An external commit during
collection invalidates the entire extraction rather than yielding mixed snapshots.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

PAGE_SIZE = 200
TERMINAL = {
    "completed",
    "failed",
    "skipped_external_handled",
    "expired_before_analysis",
}
OPTION_KEYS = (
    "normalization_review_mode",
    "refresh_normalization",
    "tenant_policy_enabled",
    "tenant_policy_advisor_enabled",
    "tenant_policy_signal_providers_enabled",
)
MEASUREMENT_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "provider_call_count",
    "usage_measurement_status",
    "output_quality_status",
    "repair_applied",
    "deterministic_fallback_used",
    "degraded_section_count",
)
SUMMARY_KEYS = (
    "analysis_status",
    "total_duration_ms",
    "model_name",
    "prompt_version",
    "base_verdict",
    "effective_verdict",
    "recommended_handling",
    "decision_usable",
    "base_model_evaluated",
    "processing_path",
    "pattern_support_count",
    "candidate_created",
    "pattern_quality_gate",
)
USE_KEYS = ("memory_id", "memory_version", "effect", "directive_applied")
GENERIC_REJECTION_REASONS = {"审核人决定放弃沉淀该候选，未形成可复用 Memory。"}
BOOLEAN_SUMMARY_KEYS = {
    "base_model_evaluated",
    "decision_usable",
    "candidate_created",
    "pattern_quality_gate",
}


class ReportDataError(ValueError):
    """The source is unavailable, ambiguous, changing, or incomplete."""


def _json(value, default=None):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


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


def _before(value, cutoff):
    parsed = _time(value)
    return parsed is not None and parsed <= cutoff


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), PAGE_SIZE):
        yield values[start : start + PAGE_SIZE]


def _safe_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:256]
    return None


def _use_summary(use):
    result = {key: _safe_scalar(use.get(key)) for key in USE_KEYS}
    if (
        result["directive_applied"] in (0, 1)
        and result["directive_applied"] is not None
    ):
        result["directive_applied"] = bool(result["directive_applied"])
    report = use.get("applicability_report") or {}
    result["applicability_status"] = _safe_scalar(
        report.get("status", use.get("applicability_status"))
    )
    codes = report.get("reason_codes", use.get("applicability_reason_codes"))
    result["applicability_reason_codes"] = (
        [_safe_scalar(value) for value in codes[:40]] if isinstance(codes, list) else []
    )
    return result


class _Reader:
    def __init__(self, conn, timeout):
        self.conn = conn
        self.timeout = timeout

    def read(self, sql, params=()):
        deadline = time.monotonic() + self.timeout
        self.conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            with closing(self.conn.execute(sql, params)) as cursor:
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            name = getattr(exc, "sqlite_errorname", type(exc).__name__)
            message = str(exc)
            if name == "SQLITE_INTERRUPT":
                detail = "单次查询超时，可适当提高 --query-timeout（最多 60 秒）后重试"
            elif name in {"SQLITE_BUSY", "SQLITE_LOCKED"}:
                detail = "数据库繁忙，请暂停运行与审核写入后稍后重试"
            elif message.startswith(("no such table:", "no such column:")):
                identifier = message.split(":", 1)[1].strip()
                safe_identifier = "".join(
                    char for char in identifier if char.isalnum() or char in "_."
                )[:128]
                detail = f"数据库字段不兼容（{safe_identifier}）；工具不自动迁移数据库"
            elif "malformed JSON" in message:
                detail = "保存的 JSON 字段无法解析，请检查源记录；工具不修复原始数据"
            else:
                detail = "数据库读取失败，请核对数据库版本和可读状态"
            raise ReportDataError(
                f"只读查询未完成（{name}）：{detail}；不会输出部分报告。"
            ) from exc
        finally:
            self.conn.set_progress_handler(None, 0)

    def pages(self, sql, params=()):
        offset = 0
        while True:
            page = self.read(sql + " LIMIT ? OFFSET ?", (*params, PAGE_SIZE, offset))
            yield from page
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)

    def by_ids(self, sql, ids):
        for chunk in _chunks(sorted(set(ids))):
            yield from self.pages(sql.format(ids=",".join("?" for _ in chunk)), chunk)


def _rounds(reader, experiment_id):
    rows = list(
        reader.pages(
            """
        SELECT round_id, experiment_id, batch, state, created_at,
          json_extract(record_payload,'$.selection') AS selection,
          json_extract(record_payload,'$.options') AS options,
          json_extract(record_payload,'$.memory_mode') AS memory_mode,
          json_extract(record_payload,'$.config_hash') AS config_hash,
          json_extract(record_payload,'$.memory_snapshot') AS memory_snapshot,
          json_extract(record_payload,'$.superseded_by_round_id') AS superseded_by_round_id
        FROM soc_corpus_rounds WHERE experiment_id=? ORDER BY created_at, round_id
    """,
            (experiment_id,),
        )
    )
    for row in rows:
        for key, default in (
            ("selection", {}),
            ("options", {}),
            ("memory_snapshot", []),
        ):
            row[key] = _json(row[key], default)
        row["options"] = {key: row["options"].get(key) for key in OPTION_KEYS}
        if _time(row["created_at"]) is None:
            raise ReportDataError(
                f"轮次 {row['round_id']} 缺少可靠创建时间，不能自动确定统计范围。"
            )
    return rows


def _choose_round(reader, rounds, validation_ids, requested):
    candidates = []
    for row in rounds:
        selection = row["selection"]
        if row["batch"] != "validation" or selection.get("scope") != "all":
            continue
        if row["superseded_by_round_id"]:
            continue
        if any(selection.get(key) for key in ("group_ids", "rule_codes", "alert_ids")):
            continue
        candidates.append(row)
    if requested:
        candidates = [row for row in candidates if row["round_id"] == requested]
    if not candidates:
        raise ReportDataError(
            "没有符合条件的未被替代的全量第二批轮次；不能把最后一条单独重跑当作整批。"
        )
    candidates.sort(
        key=lambda row: (_time(row["created_at"]), row["round_id"]), reverse=True
    )
    selected = candidates[0]
    if (
        not requested
        and len(candidates) > 1
        and _time(candidates[1]["created_at"]) == _time(selected["created_at"])
    ):
        raise ReportDataError(
            "多个全量轮次的创建时间相同，请用 --round 明确选择："
            + ", ".join(row["round_id"] for row in candidates)
        )
    actual = {
        row["alert_id"]
        for row in reader.pages(
            "SELECT alert_id FROM soc_corpus_round_items WHERE round_id=? ORDER BY sequence_number",
            (selected["round_id"],),
        )
    }
    if actual != validation_ids:
        raise ReportDataError(
            f"最新全量轮次 {selected['round_id']} 实际覆盖 {len(actual)} 条，第二批成员为 {len(validation_ids)} 条；不能回退旧轮次。"
        )
    return selected


def _job_rows(reader, members, rounds, selected):
    # Resolve membership with scalar columns before decoding selected summaries.
    # Do not force SQLite to parse every historical result before each LIMIT.
    query = """
        SELECT i.round_id, i.alert_id, i.group_id, i.sequence_number,
          j.job_id, j.status, j.run_id, j.attempt_count, j.error_code,
          j.started_at, j.completed_at, j.updated_at
        FROM soc_corpus_round_items i
        LEFT JOIN soc_processing_jobs j ON j.job_id=i.job_id
        WHERE i.round_id=? AND i.sequence_number>? ORDER BY i.sequence_number LIMIT ?
    """

    def source_rows():
        cutoff = _time(selected["created_at"])
        for round_ in rounds.values():
            if (
                round_["batch"] != "learning"
                and round_["round_id"] != selected["round_id"]
                and _time(round_["created_at"]) <= cutoff
            ):
                continue
            last_sequence = -1
            while True:
                page = reader.read(
                    query, (round_["round_id"], last_sequence, PAGE_SIZE)
                )
                if not page:
                    break
                yield from page
                last_sequence = page[-1]["sequence_number"]
                if len(page) < PAGE_SIZE:
                    break

    for raw in source_rows():
        member = members.get(raw["alert_id"])
        if member is None or raw["job_id"] is None:
            raise ReportDataError("轮次成员或任务引用不完整，停止生成报告。")
        row = {
            **member,
            **{
                key: raw[key]
                for key in (
                    "round_id",
                    "job_id",
                    "status",
                    "run_id",
                    "attempt_count",
                    "error_code",
                    "started_at",
                    "completed_at",
                    "updated_at",
                )
            },
        }
        row["options"] = rounds[row["round_id"]]["options"]
        row["round_created_at"] = rounds[row["round_id"]]["created_at"]
        row["snapshot_changed_during_run"] = None
        row["candidate_id"] = row["observation_id"] = None
        row["summary"] = {"memory_uses": None, "measurements": {}, "phase_timings": []}
        row["label"] = {
            "scorable": False,
            "expected_handling": None,
            "reason": "historical_label_unavailable",
        }
        row["semantic_review"] = {
            "status": None,
            "mode": None,
            "change_count": None,
            "observation_change_count": None,
            "issue_count": None,
        }
        yield row


def _enrich_jobs(reader, rows):
    by_id = {row["job_id"]: row for row in rows if row.get("as_of_status") != "unknown"}
    summary_fields = ",".join(
        f"json_extract(result_payload,'$.summary.{key}') AS s_{key}"
        for key in SUMMARY_KEYS
    )
    for raw in reader.by_ids(
        f"""
        SELECT job_id,
          json_extract(result_payload,'$.candidate_id') AS candidate_id,
          json_extract(result_payload,'$.observation_id') AS observation_id,
          json_extract(result_payload,'$.snapshot_changed_during_run') AS snapshot_changed,
          json_extract(result_payload,'$.summary.memory_uses') AS memory_uses,
          json_extract(result_payload,'$.summary.measurements') AS measurements,
          json_extract(result_payload,'$.summary.phase_timings') AS phase_timings,
          {summary_fields}
        FROM soc_processing_jobs WHERE job_id IN ({{ids}})
    """,
        by_id,
    ):
        row = by_id[raw["job_id"]]
        row["candidate_id"], row["observation_id"] = (
            raw["candidate_id"],
            raw["observation_id"],
        )
        row["snapshot_changed_during_run"] = bool(raw["snapshot_changed"])
        row["summary"] = {key: _safe_scalar(raw[f"s_{key}"]) for key in SUMMARY_KEYS}
        for key in BOOLEAN_SUMMARY_KEYS:
            value = row["summary"][key]
            row["summary"][key] = (
                bool(value) if value in (0, 1) and value is not None else None
            )
        uses = _json(raw["memory_uses"])
        row["summary"]["memory_uses"] = (
            None if uses is None else [_use_summary(use) for use in uses]
        )
        measures = _json(raw["measurements"], {})
        row["summary"]["measurements"] = {
            key: _safe_scalar(measures.get(key)) for key in MEASUREMENT_KEYS
        }
        row["summary"]["phase_timings"] = [
            {
                key: _safe_scalar(step.get(key))
                for key in ("phase", "status", "duration_ms")
            }
            for step in _json(raw["phase_timings"], [])
        ]


def _enrich_runs(reader, rows, progress):
    by_run = defaultdict(list)
    for row in rows:
        if row["run_id"] and row.get("as_of_status") != "unknown":
            by_run[row["run_id"]].append(row)
    fields = ",".join(MEASUREMENT_KEYS)
    count = 0
    for raw in reader.by_ids(
        f"""
        SELECT run_id,total_duration_ms,{fields},
          json_extract(run_payload,'$.normalization_assistance.status') AS semantic_status,
          json_extract(run_payload,'$.normalization_assistance.mode') AS semantic_mode,
          json_array_length(run_payload,'$.normalization_assistance.changes') AS change_count,
          json_array_length(run_payload,'$.normalization_assistance.observation_changes') AS observation_change_count,
          json_array_length(run_payload,'$.normalization_assistance.issues') AS issue_count
        FROM soc_analysis_runs WHERE run_id IN ({{ids}})
    """,
        by_run,
    ):
        for row in by_run[raw["run_id"]]:
            if row["summary"].get("total_duration_ms") is None:
                row["summary"]["total_duration_ms"] = raw["total_duration_ms"]
            for key in MEASUREMENT_KEYS:
                if row["summary"]["measurements"].get(key) is None:
                    row["summary"]["measurements"][key] = raw[key]
            row["semantic_review"] = {
                "status": raw["semantic_status"],
                "mode": raw["semantic_mode"],
                **{
                    key: raw[key]
                    for key in (
                        "change_count",
                        "observation_change_count",
                        "issue_count",
                    )
                },
            }
        count += 1
        if count % 1000 == 0:
            progress(f"已读取 {count} 条固定 Run 的轻量测量与语义核对摘要……")
    # The durable uses table is authoritative for persisted uses. Missing summary
    # plus no rows remains unknown, because absent legacy evidence is not a zero.
    use_map = defaultdict(list)
    for use in reader.by_ids(
        """SELECT run_id,memory_id,memory_version,effect,directive_applied,
          json_extract(use_payload,'$.applicability_report.status') AS applicability_status,
          json_extract(use_payload,'$.applicability_report.reason_codes') AS applicability_reason_codes
          FROM soc_memory_uses WHERE run_id IN ({ids})""",
        by_run,
    ):
        use["applicability_reason_codes"] = _json(use["applicability_reason_codes"], [])
        use_map[use.pop("run_id")].append(_use_summary(use))
    for run_id, uses in use_map.items():
        for row in by_run[run_id]:
            row["summary"]["memory_uses"] = sorted(
                uses, key=lambda use: (use["memory_id"], use["memory_version"])
            )


def _candidates(reader, experiment_id, all_learning, members, cutoff):
    run_members = {
        row["run_id"]: row["alert_id"] for row in all_learning if row["run_id"]
    }
    query = """
        SELECT c.candidate_id,c.status AS current_status,c.source_run_id,c.source_alert_id,
          c.created_at,c.updated_at,c.reviewed_at,
          CASE WHEN c.status='rejected' THEN substr(json_extract(c.candidate_payload,'$.review_reason'),1,1001) END AS review_reason,
          json_extract(c.candidate_payload,'$.reviewed_verdict') AS reviewed_verdict,
          json_extract(c.candidate_payload,'$.metadata.observation_ids') AS observations,
          json_extract(c.candidate_payload,'$.source.metadata.observation_ids') AS source_observations
        FROM soc_memory_candidates c WHERE
          c.source_run_id IN (
            SELECT j.run_id FROM soc_processing_jobs j
            JOIN soc_corpus_round_items i ON i.job_id=j.job_id
            JOIN soc_corpus_rounds r ON r.round_id=i.round_id
            WHERE r.experiment_id=? AND r.batch='learning' AND j.run_id IS NOT NULL
          ) OR json_extract(c.candidate_payload,'$.metadata.experiment_id')=?
            OR json_extract(c.candidate_payload,'$.source.metadata.experiment_id')=?
        ORDER BY c.candidate_id
    """
    values = list(reader.pages(query, (experiment_id, experiment_id, experiment_id)))
    observation_ids = {
        value
        for row in values
        for key in ("observations", "source_observations")
        for value in _json(row[key], [])
    }
    observations = {
        row["observation_id"]: row
        for row in reader.by_ids(
            "SELECT observation_id,run_id,alert_id FROM soc_memory_pattern_observations WHERE observation_id IN ({ids})",
            observation_ids,
        )
    }
    # A retry can replace a job's Run pointer. Resolve older observation Run IDs
    # by the immutable input hash, rather than matching only an alert number.
    historical_run_ids = {row["run_id"] for row in observations.values()} | {
        row["source_run_id"] for row in values if row["source_run_id"]
    }
    for run in reader.by_ids(
        "SELECT run_id,alert_id,input_hash FROM soc_analysis_runs WHERE run_id IN ({ids})",
        historical_run_ids - run_members.keys(),
    ):
        member = members.get(run["alert_id"])
        if (
            member
            and member["batch"] == "learning"
            and member.get("payload_hash")
            and run["input_hash"] == member["payload_hash"]
        ):
            run_members[run["run_id"]] = run["alert_id"]
    result = []
    for raw in values:
        alerts, runs = set(), set()
        if raw["source_run_id"]:
            runs.add(raw["source_run_id"])
            if raw["source_run_id"] in run_members:
                alerts.add(run_members[raw["source_run_id"]])
        ids = set(_json(raw["observations"], [])) | set(
            _json(raw["source_observations"], [])
        )
        resolved_ids = set()
        for observation_id in ids:
            obs = observations.get(observation_id)
            if (
                obs
                and obs["run_id"] in run_members
                and obs["alert_id"] == run_members[obs["run_id"]]
            ):
                alerts.add(run_members[obs["run_id"]])
                runs.add(obs["run_id"])
                resolved_ids.add(observation_id)
        as_of, evidence = "unknown", "current_record_changed_or_time_unavailable"
        if _time(raw["created_at"]) and _time(raw["created_at"]) > cutoff:
            as_of, evidence = "not_created", "created_after_round"
        elif _before(raw["created_at"], cutoff) and _before(raw["updated_at"], cutoff):
            as_of, evidence = (
                raw["current_status"],
                "current_record_last_updated_before_round",
            )
        reason = raw["review_reason"]
        if not isinstance(reason, str) or reason.strip() in GENERIC_REJECTION_REASONS:
            reason = None
        reason_truncated = bool(reason and len(reason) > 1000)
        reason = reason[:1000] if reason else None
        result.append(
            {
                "candidate_id": raw["candidate_id"],
                "current_status": raw["current_status"],
                "created_at": raw["created_at"],
                "reviewed_at": raw["reviewed_at"],
                "as_of_status": as_of,
                "as_of_evidence": evidence,
                "reason": reason,
                "reason_truncated": reason_truncated,
                "current_reviewed_verdict": _safe_scalar(raw["reviewed_verdict"]),
                "as_of_reviewed_verdict": _safe_scalar(raw["reviewed_verdict"])
                if evidence == "current_record_last_updated_before_round"
                else None,
                "source_alert_ids": sorted(alerts),
                "source_run_ids": sorted(runs),
                "group_ids": sorted(
                    {members[alert_id]["group_id"] for alert_id in alerts}
                ),
                "memory_ids": [],
                "unresolved_observation_count": len(ids - resolved_ids),
                "source_groups_complete": bool(ids) and resolved_ids == ids,
                "snapshot_memory_ids": [],
            }
        )
    return result


def _memories(reader, snapshot, candidates, rows, cutoff):
    by_candidate = {row["candidate_id"]: row for row in candidates}
    pairs = {(item["memory_id"], item["version"]): item for item in snapshot}
    wanted = set(pairs)
    for row in rows:
        wanted.update(
            (use["memory_id"], use["memory_version"])
            for use in row["summary"].get("memory_uses") or []
            if use.get("memory_id") and use.get("memory_version")
        )
    query = """
        SELECT memory_id,version,source_candidate_id,status,retrieval_enabled,
          created_at,updated_at,content_hash,facets_hash,
          json_extract(record_payload,'$.reviewed_verdict') AS reviewed_verdict
        FROM soc_memory_records WHERE memory_id IN ({ids})
    """
    current = {
        row["memory_id"]: row
        for row in reader.by_ids(query, [item[0] for item in wanted])
    }
    # Include records arising from first-batch candidates even if absent from the
    # frozen snapshot; that absence must not be confused with "no approved record".
    for row in reader.by_ids(
        query.replace("memory_id IN", "source_candidate_id IN"), by_candidate
    ):
        current[row["memory_id"]] = row
        if not any(pair[0] == row["memory_id"] for pair in wanted):
            wanted.add((row["memory_id"], row["version"]))
    result = []
    for memory_id, version in sorted(wanted):
        raw = current.get(memory_id, {})
        frozen = pairs.get((memory_id, version))
        candidate = by_candidate.get(raw.get("source_candidate_id"))
        unchanged = bool(
            frozen
            and raw.get("version") == version
            and raw.get("content_hash") == frozen.get("content_hash")
            and raw.get("facets_hash") == frozen.get("facets_hash")
            and _before(raw.get("updated_at"), cutoff)
        )
        row = {
            "memory_id": memory_id,
            "memory_version": version,
            "source_candidate_id": raw.get("source_candidate_id"),
            "source_group_ids": candidate["group_ids"] if candidate else [],
            "source_groups_complete": bool(
                candidate and candidate["source_groups_complete"]
            ),
            "snapshot_included": frozen is not None,
            "current_version": raw.get("version"),
            "current_status": raw.get("status"),
            "current_retrieval_enabled": bool(raw["retrieval_enabled"])
            if raw
            else None,
            "as_of_status": "snapshot_eligible" if frozen else "unknown",
            "reviewed_verdict": raw.get("reviewed_verdict") if unchanged else None,
            "historical_verdict_known": unchanged,
        }
        result.append(row)
        if candidate:
            candidate["memory_ids"].append(memory_id)
            candidate["current_reviewed_verdict"] = raw.get("reviewed_verdict")
            if frozen:
                candidate["snapshot_memory_ids"].append(memory_id)
            if unchanged:
                candidate["as_of_reviewed_verdict"] = raw.get("reviewed_verdict")
    for candidate in candidates:
        candidate["memory_ids"] = sorted(set(candidate["memory_ids"]))
        candidate["snapshot_memory_ids"] = sorted(set(candidate["snapshot_memory_ids"]))
    return result


def collect(
    db: Path,
    experiment_id: str | None = None,
    round_id: str | None = None,
    progress: Callable[[str], None] = lambda _message: None,
    query_timeout: float = 30,
) -> dict:
    """Collect a completed, fixed whole-validation round without mutating SQLite."""
    if not math.isfinite(query_timeout) or query_timeout <= 0:
        raise ValueError("query_timeout must be positive and finite")
    db = Path(db).resolve(strict=True)
    if not db.is_file():
        raise ReportDataError("数据库路径不是普通文件。")
    started = datetime.now(UTC).isoformat()
    with closing(
        sqlite3.connect(
            db.as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=min(query_timeout, 3),
        )
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON").close()
        reader = _Reader(conn, query_timeout)
        initial = reader.read("PRAGMA data_version")[0]["data_version"]
        progress("只读确认实验和第二批全量轮次……")
        experiments = list(
            reader.pages(
                "SELECT experiment_id,plan_id,manifest_hash,json_extract(record_payload,'$.source_identity') AS source_identity FROM soc_corpus_experiments WHERE experiment_id IN (SELECT experiment_id FROM soc_corpus_rounds WHERE batch='validation') ORDER BY experiment_id"
            )
        )
        for row in experiments:
            row["source_identity"] = _json(row["source_identity"], {})
        if experiment_id is None:
            if len(experiments) != 1:
                raise ReportDataError(
                    "请用 --experiment 选择含第二批的实验："
                    + ", ".join(row["experiment_id"] for row in experiments)
                )
            experiment_id = experiments[0]["experiment_id"]
        experiment = next(
            (row for row in experiments if row["experiment_id"] == experiment_id), None
        )
        if experiment is None:
            raise ReportDataError("指定实验不存在或没有第二批轮次。")
        members = list(
            reader.pages(
                """
            SELECT alert_id,group_id,batch,validation_tier,rule_code,sequence_number,
              json_extract(record_payload,'$.detection_key') AS detection_key,
              json_extract(record_payload,'$.payload_hash') AS payload_hash,
              json_extract(record_payload,'$.event_time') AS event_time
            FROM soc_corpus_experiment_members WHERE experiment_id=? ORDER BY sequence_number
        """,
                (experiment_id,),
            )
        )
        member_map = {row["alert_id"]: row for row in members}
        validation_ids = {
            row["alert_id"] for row in members if row["batch"] == "validation"
        }
        rounds = _rounds(reader, experiment_id)
        selected = _choose_round(reader, rounds, validation_ids, round_id)
        cutoff = _time(selected["created_at"])
        progress(f"固定统计轮次：{selected['round_id']}；读取任务摘要……")
        all_rows = list(
            _job_rows(
                reader, member_map, {row["round_id"]: row for row in rounds}, selected
            )
        )
        validation = [
            row for row in all_rows if row["round_id"] == selected["round_id"]
        ]
        unfinished = [row for row in validation if row["status"] not in TERMINAL]
        if unfinished:
            raise ReportDataError(
                f"本轮仍有 {len(unfinished)} 条未结束任务（含排队），请等整批完成后统计，不能回退旧轮次。"
            )
        learning_all = [row for row in all_rows if row["batch"] == "learning"]
        learning_latest = {}
        for row in sorted(
            learning_all,
            key=lambda row: (_time(row["round_created_at"]), row["round_id"]),
        ):
            if _time(row["round_created_at"]) < cutoff:
                learning_latest[row["alert_id"]] = row
        for row in learning_latest.values():
            known = (
                row["status"] in TERMINAL
                and _before(row["completed_at"], cutoff)
                and _before(row["updated_at"], cutoff)
            )
            row["as_of_status"] = row["status"] if known else "unknown"
            if not known:
                row["summary"] = {
                    "memory_uses": None,
                    "measurements": {},
                    "phase_timings": [],
                }
        followups = [
            row
            for row in all_rows
            if row["batch"] == "validation" and _time(row["round_created_at"]) > cutoff
        ]
        fixed_rows = [*validation, *learning_latest.values(), *followups]
        _enrich_jobs(reader, fixed_rows)
        _enrich_runs(reader, fixed_rows, progress)
        progress("关联第一批候选来源与冻结经验清单……")
        candidates = _candidates(
            reader, experiment_id, learning_all, member_map, cutoff
        )
        memories = _memories(
            reader, selected["memory_snapshot"], candidates, fixed_rows, cutoff
        )
        schemas = reader.read("SELECT version_num FROM soc_alembic_version")
        final = reader.read("PRAGMA data_version")[0]["data_version"]
        if initial != final:
            raise ReportDataError(
                "读取期间数据库发生写入，已拒绝混合快照。请停止运行与审核后重试；不会交付部分报告。"
            )
        identity = {
            **experiment,
            "round_id": selected["round_id"],
            "options": selected["options"],
            "memory_snapshot": selected["memory_snapshot"],
            "run_ids": sorted((row["alert_id"], row["run_id"]) for row in validation),
        }
        return {
            "metadata": {
                "schema_version": "soc.validation_report_data.v1",
                **experiment,
                "round_id": selected["round_id"],
                "round_created_at": selected["created_at"],
                "round_state": selected["state"],
                "options": selected["options"],
                "memory_mode": selected["memory_mode"],
                "memory_snapshot": selected["memory_snapshot"],
                "config_hash": selected["config_hash"],
                "member_count": len(members),
                "validation_count": len(validation),
                "read_started_at": started,
                "read_finished_at": datetime.now(UTC).isoformat(),
                "source_db_name": db.name,
                "source_schema_revision": [row["version_num"] for row in schemas],
                "source_data_version": initial,
                "scope_sha256": hashlib.sha256(
                    json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "consistency": "short_autocommit_pages_unchanged_data_version",
            },
            "members": members,
            "learning_rows": list(learning_latest.values()),
            "validation_rows": validation,
            "followup_rows": followups,
            "candidates": candidates,
            "memories": memories,
            "warnings": [
                "历史处置标签不在本次独立数据库提取范围内，不计算处置一致率。",
                "候选审核为当前状态与有证据的运行时状态并列；无法还原时标为 unknown。",
                "耗时与 Token 为选定任务当前固定 Run，不包含未能关联的历史失败尝试；不能视为完整账单。",
            ],
        }
