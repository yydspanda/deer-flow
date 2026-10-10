#!/usr/bin/env python3
"""Export first-batch candidate evidence without application imports or DB writes.

Only saved metadata is measured. This does not rebuild signatures, run eligibility
policy, compare operational labels, or infer missing candidate reasons.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PAGE_SIZE = 200
MAX_ROWS = 200_000
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
KNOWN_REASONS = {
    "direct_resolution_not_an_independent_sample": "direct_resolution_not_an_independent_sample",
    "validation_learning_disabled": "validation_learning_disabled",
    "manual candidate already governs this Pattern lineage; automatic candidate creation is suppressed": "lineage_governance",
    "duplicate operational occurrence retained as one observation": "duplicate_occurrence",
    "duplicate alert source retained as one observation": "duplicate_source",
    "observation retained; repeated-pattern thresholds not reached": "threshold_not_reached",
    "equivalent lesson governs current reinforcement observations": "equivalent_lesson",
    "candidate snapshot is frozen; later observations remain replay-only": "candidate_frozen",
    "one frozen pending repeated-pattern candidate created for human review": "candidate_created",
    "existing governed candidate reused after concurrent proposal": "concurrent_candidate_reused",
    "only completed Runtime results may become memory pattern observations": "runtime_not_completed",
    "completed Runtime result has no bounded analysis request": "bounded_request_absent",
    "memory pattern aggregation requires an explicit tenant_id": "tenant_absent",
    "memory pattern aggregation requires a completed analysis conclusion": "conclusion_absent",
    "memory pattern aggregation requires the original Runtime input payload": "runtime_input_absent",
    "memory pattern aggregation could not reconstruct canonical event time": "event_time_unavailable",
    "memory pattern aggregation requires canonical alert event_time": "event_time_absent",
    "canonical alert event_time must be timezone-aware": "event_time_naive",
    "PingAn memory profile requires a canonical detection key or behavior fingerprint": "detection_and_behavior_absent",
    "研判已完成；实体匹配条件超过 512 字符，本次未生成可复用经验。": "exact_facet_too_long",
}


class AuditError(ValueError):
    """Source scope or evidence cannot be exported safely."""


def _json(value, default=None):
    return default if value is None else json.loads(value)


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=UTC)
            if parsed.tzinfo is None
            else parsed.astimezone(UTC)
        )
    except (AttributeError, ValueError) as exc:
        raise AuditError("时间字段无效，不能确定固定执行顺序") from exc


def _counts(values):
    return dict(
        sorted(Counter("unknown" if v is None else str(v) for v in values).items())
    )


def _reason(value):
    if value is None:
        return "unknown_not_recorded"
    if value in KNOWN_REASONS:
        return KNOWN_REASONS[value]
    if value.startswith(
        "recurrence threshold reached, but lesson quality gate withheld expert review: "
    ):
        return "quality_gate_withheld"
    return "unknown_saved_reason_sha256:" + hashlib.sha256(value.encode()).hexdigest()


class Reader:
    def __init__(self, conn, timeout):
        self.conn, self.timeout = conn, timeout
        self.version = self.data_version()

    def data_version(self):
        with closing(self.conn.execute("PRAGMA data_version")) as cursor:
            return cursor.fetchone()[0]

    def verify(self):
        if self.data_version() != self.version:
            raise AuditError(
                "数据库在采集期间发生变化；请暂停运行和审核后重试，不输出混合快照"
            )

    def read(self, sql, params=()):
        deadline = time.monotonic() + self.timeout
        self.conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            with closing(self.conn.execute(sql, params)) as cursor:
                rows = cursor.fetchmany(PAGE_SIZE + 1)
            if len(rows) > PAGE_SIZE:
                raise AuditError("单次查询超过分页边界")
            self.verify()
            return [dict(row) for row in rows]
        finally:
            self.conn.set_progress_handler(None, 0)

    def pages(self, sql, params=()):
        offset = 0
        while True:
            rows = self.read(sql + " LIMIT ? OFFSET ?", (*params, PAGE_SIZE, offset))
            offset += len(rows)
            if offset > MAX_ROWS:
                raise AuditError("统计范围超过 200000 项边界；不输出截断报告")
            yield from rows
            if len(rows) < PAGE_SIZE:
                return

    def by_ids(self, sql, ids):
        ids = sorted(set(ids))
        for start in range(0, len(ids), PAGE_SIZE):
            part = ids[start : start + PAGE_SIZE]
            yield from self.pages(sql.format(ids=",".join("?" for _ in part)), part)


def _fields(column, mapping):
    return ",".join(
        f"json_extract({column},'{path}') AS {name}" for name, path in mapping.items()
    )


def _job_summary(rows):
    return {
        "count": len(rows),
        "job_status": _counts(row["status"] for row in rows),
        "source_type": _counts(row.get("source_type") for row in rows),
        "model_name": _counts(row.get("model_name") for row in rows),
        "prompt_version": _counts(row.get("prompt_version") for row in rows),
        "semantic_status": _counts(row.get("semantic_status") for row in rows),
        "semantic_mode": _counts(row.get("semantic_mode") for row in rows),
        "pattern_reason": _counts(row["pattern_reason_code"] for row in rows),
        "saved_quality_gate": _counts(row["quality_gate"] for row in rows),
        "saved_quality_reason_codes": _counts(
            code for row in rows for code in row["quality_reason_codes"] or []
        ),
        "saved_quality_reason_codes_missing": sum(
            row["quality_reason_codes"] is None for row in rows
        ),
        "support_count_at_execution": _counts(row["support_count"] for row in rows),
        "observation_ref_present": sum(bool(row["observation_id"]) for row in rows),
        "candidate_ref_present": sum(bool(row["candidate_id"]) for row in rows),
        "core_component_count": _counts(row["core_component_count"] for row in rows),
        "behavior_strength": _counts(row["behavior_strength"] for row in rows),
        "projection_audit_present": sum(
            row.get("projection_usage_count") is not None for row in rows
        ),
        "saved_projection_gaps": _counts(
            code for row in rows for code in row.get("projection_gaps") or []
        ),
        "projection_profile_versions": _counts(
            row.get("projection_profile_version") for row in rows
        ),
    }


def collect(database, *, experiment_id=None, query_timeout=30):
    database = Path(database).resolve()
    if not database.is_file():
        raise AuditError("DEV 数据库不存在；工具不会创建数据库")
    if not 0 < query_timeout <= 60:
        raise AuditError("query-timeout 必须在 0 到 60 秒之间")
    with closing(
        sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2
        )
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON").close()
        reader = Reader(conn, query_timeout)
        experiments = list(
            reader.pages(
                "SELECT experiment_id FROM soc_corpus_experiments ORDER BY experiment_id"
            )
        )
        available = [row["experiment_id"] for row in experiments]
        if experiment_id is None:
            if len(available) != 1:
                raise AuditError(
                    "实验不唯一，请用 --experiment 指定：" + ", ".join(available)
                )
            experiment_id = available[0]
        if experiment_id not in available:
            raise AuditError("指定实验不存在，不回退到其他实验")
        experiment = reader.read(
            """SELECT experiment_id,plan_id,created_at,
            json_extract(record_payload,'$.environment') AS environment,
            json_extract(record_payload,'$.tenant_id') AS tenant_id
            FROM soc_corpus_experiments WHERE experiment_id=?""",
            (experiment_id,),
        )[0]
        if (
            experiment["environment"] != "dev-corpus-eval"
            or experiment["tenant_id"] != "pingan"
        ):
            raise AuditError(
                "仅支持 PingAn DEV 语料实验（dev-corpus-eval）；不会读取其他运行环境"
            )
        members = list(
            reader.pages(
                """SELECT alert_id,group_id,rule_code,sequence_number FROM soc_corpus_experiment_members
            WHERE experiment_id=? AND batch='learning' ORDER BY sequence_number,alert_id""",
                (experiment_id,),
            )
        )
        if not members:
            raise AuditError("所选实验没有第一批固定成员")
        rounds = list(
            reader.pages(
                """SELECT round_id,state,created_at,
            json_extract(record_payload,'$.config_hash') AS config_hash,
            json_extract(record_payload,'$.options') AS options
            FROM soc_corpus_rounds WHERE experiment_id=? AND batch='learning' ORDER BY created_at,round_id""",
                (experiment_id,),
            )
        )
        round_map = {row["round_id"]: row for row in rounds}
        for row in rounds:
            _stamp(row["created_at"])
            options = _json(row["options"], {})
            row["options"] = {key: options.get(key) for key in OPTION_KEYS}
        job_fields = _fields(
            "j.result_payload",
            {
                "observation_id": "$.observation_id",
                "candidate_id": "$.candidate_id",
                "pattern_reason": "$.pattern_reason",
                "support_count": "$.summary.pattern_support_count",
                "candidate_created": "$.summary.candidate_created",
                "quality_gate": "$.summary.pattern_quality_gate",
                "quality_reason_codes": "$.summary.pattern_reason_codes",
                "model_name": "$.summary.model_name",
                "prompt_version": "$.summary.prompt_version",
                "analysis_status": "$.summary.analysis_status",
                "behavior_strength": "$.summary.actual_pattern.facets.behavior_strength[0]",
            },
        )
        jobs = list(
            reader.pages(
                f"""SELECT i.round_id,i.alert_id,i.job_id,j.status,j.run_id,j.error_code,
            j.completed_at,j.updated_at,{job_fields},
            json_array_length(j.result_payload,'$.summary.actual_pattern.facets.behavior_component_core') AS core_component_count
            FROM soc_corpus_round_items i JOIN soc_corpus_rounds r ON r.round_id=i.round_id
            LEFT JOIN soc_processing_jobs j ON j.job_id=i.job_id
            WHERE r.experiment_id=? AND r.batch='learning' ORDER BY i.round_id,i.sequence_number,i.alert_id""",
                (experiment_id,),
            )
        )
        if any(row["status"] not in TERMINAL for row in jobs):
            raise AuditError("第一批仍有未结束或缺失任务，请完成或暂停并处理任务后重试")
        member_map = {row["alert_id"]: row for row in members}
        if set(row["alert_id"] for row in jobs) != set(member_map):
            raise AuditError("第一批固定成员与已执行成员不一致，不能报告为全量完成")
        for row in jobs:
            row["pattern_reason_code"] = _reason(row.pop("pattern_reason"))
            row["quality_reason_codes"] = _json(row["quality_reason_codes"])
            row["rule_code"] = member_map[row["alert_id"]]["rule_code"]
            row["group_id"] = member_map[row["alert_id"]]["group_id"]
        by_run = defaultdict(list)
        for row in jobs:
            if row["run_id"]:
                by_run[row["run_id"]].append(row)
        run_fields = _fields(
            "r.run_payload",
            {
                "semantic_status": "$.normalization_assistance.status",
                "semantic_mode": "$.normalization_assistance.mode",
                "projection_gaps": "$.llm_analysis_request.memory_projection_audit.projection_gaps",
                "projection_profile_version": "$.llm_analysis_request.memory_profile.profile_version",
            },
        )
        found_runs = set()
        for row in reader.by_ids(
            f"""SELECT r.run_id,{run_fields},s.source_type,s.source_system,s.detection_key,
            r.model_name AS runtime_model_name,r.prompt_version AS runtime_prompt_version,
            json_array_length(r.run_payload,'$.llm_analysis_request.memory_projection_audit.usages') AS projection_usage_count
            FROM soc_analysis_runs r LEFT JOIN soc_alert_summaries s ON s.run_id=r.run_id
            WHERE r.run_id IN ({{ids}}) ORDER BY r.run_id""",
            by_run,
        ):
            found_runs.add(row["run_id"])
            row["projection_gaps"] = _json(row["projection_gaps"])
            for job in by_run[row["run_id"]]:
                job.update(row)
                job["model_name"] = job["model_name"] or row["runtime_model_name"]
                job["prompt_version"] = (
                    job["prompt_version"] or row["runtime_prompt_version"]
                )
        latest = {}
        for job in jobs:
            alert_id = job["alert_id"]
            prior = latest.get(alert_id)
            current_time = _stamp(round_map[job["round_id"]]["created_at"])
            if prior is not None:
                previous_time = _stamp(round_map[prior["round_id"]]["created_at"])
                if current_time == previous_time:
                    raise AuditError("同一告警多轮次创建时间相同，无法确定最后尝试")
                if current_time < previous_time:
                    continue
            latest[alert_id] = job
        observations = _observations(
            reader,
            experiment_id,
            by_run,
            [j["observation_id"] for j in jobs if j["observation_id"]],
        )
        for observation in observations:
            source_job = by_run.get(observation["run_id"], [])
            observation["source_group_id"] = (
                source_job[0]["group_id"] if source_job else None
            )
            observation["source_rule_code"] = (
                source_job[0]["rule_code"] if source_job else None
            )
        candidates = _candidates(
            reader,
            experiment_id,
            by_run,
            [j["candidate_id"] for j in jobs if j["candidate_id"]],
        )
        audits = _audits(reader, observations)
        for candidate in candidates:
            source_job = by_run.get(candidate["source_run_id"], [])
            candidate["alert_source_type"] = (
                source_job[0].get("source_type") if source_job else None
            )
        gates = _last_gates(audits)
        final_rows = list(latest.values())
        summary = {
            "schema_version": "soc.learning_candidate_audit.v1",
            "collected_at": datetime.now(UTC).isoformat(),
            "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sqlite_version": sqlite3.sqlite_version,
            "database_file": database.name,
            "experiment": experiment,
            "scope": {
                "learning_members": len(members),
                "learning_groups": len({r["group_id"] for r in members}),
                "round_count": len(rounds),
                "attempt_count": len(jobs),
                "last_attempt_per_member_count": len(final_rows),
            },
            "rounds": [
                dict(
                    row,
                    job_status=_counts(
                        j["status"] for j in jobs if j["round_id"] == row["round_id"]
                    ),
                )
                for row in rounds
            ],
            "all_attempts": _job_summary(jobs),
            "last_attempt_per_member": _job_summary(final_rows),
            "observations": {
                "count": len(observations),
                "aggregation_keys": len({o["aggregation_key"] for o in observations}),
                "lineage_keys": len({o["lineage_key"] for o in observations}),
                "observations_per_aggregation": _counts(
                    Counter(o["aggregation_key"] for o in observations).values()
                ),
                "distinct_alerts_per_aggregation": _counts(
                    len(
                        {
                            o["alert_id"]
                            for o in observations
                            if o["aggregation_key"] == key
                        }
                    )
                    for key in {o["aggregation_key"] for o in observations}
                ),
                "core_component_count": _counts(
                    o["core_component_count"] for o in observations
                ),
                "behavior_strength": _counts(
                    o["behavior_strength"] for o in observations
                ),
                "profile_versions": _counts(
                    (o["profile_id"], o["profile_version"], o["feature_schema_version"])
                    for o in observations
                ),
                "saved_minimum_support": _counts(
                    o["minimum_support"] for o in observations
                ),
                "saved_minimum_distinct_sources": _counts(
                    o["minimum_distinct_sources"] for o in observations
                ),
                "saved_minimum_conclusive_support": _counts(
                    o["minimum_conclusive_support"] for o in observations
                ),
            },
            "candidates_current": {
                "count": len(candidates),
                "status": _counts(c["status"] for c in candidates),
                "created_on": _counts(
                    c["created_at"][:10] if c["created_at"] else None
                    for c in candidates
                ),
                "candidate_source_type": _counts(
                    c["candidate_source_type"] for c in candidates
                ),
                "source_alert_type": _counts(
                    c["alert_source_type"] for c in candidates
                ),
                "profile_versions": _counts(
                    (c["profile_id"], c["profile_version"]) for c in candidates
                ),
            },
            "last_saved_ingest_per_aggregation": {
                "count": len(gates),
                "threshold_met": _counts(g["threshold_met"] for g in gates),
                "quality_gate_passed": _counts(g["quality_gate_passed"] for g in gates),
                "quality_reason_codes": _counts(
                    code for g in gates for code in g["quality_reason_codes"] or []
                ),
                "candidate_coverage": _counts(g["candidate_coverage"] for g in gates),
                "tied_latest_audit_count": sum(g["time_tie"] for g in gates),
            },
            "evidence_gaps": {
                "run_refs_without_record": len(set(by_run) - found_runs),
                "last_attempt_without_observation_ref": sum(
                    not j["observation_id"] for j in final_rows
                ),
                "observation_refs_without_record": len(
                    {j["observation_id"] for j in jobs if j["observation_id"]}
                    - {o["observation_id"] for o in observations}
                ),
                "candidate_refs_without_record": len(
                    {j["candidate_id"] for j in jobs if j["candidate_id"]}
                    - {c["candidate_id"] for c in candidates}
                ),
                "aggregations_without_ingest_audit": len(
                    {o["aggregation_key"] for o in observations}
                    - {a["aggregation_key"] for a in audits}
                ),
            },
            "per_rule": [
                {
                    "rule_code": rule,
                    **_job_summary([j for j in final_rows if j["rule_code"] == rule]),
                }
                for rule in sorted(
                    {j["rule_code"] for j in final_rows}, key=lambda x: x or ""
                )
            ],
            "limits": [
                "Current candidate states are not historical as-of states.",
                "Candidate inventory includes explicit experiment metadata, learning source runs, or saved learning job references; it is not a global queue count.",
                "Last saved ingest quality is historical evidence, not recomputed current eligibility.",
                "Job reason and reason-code counts overlap and are not additive candidate-loss counts.",
                "Absent observation or candidate does not establish a rejection reason.",
                "No operational labels, raw logs, model reasoning, or selector values are exported.",
            ],
        }
        final_version = reader.data_version()
        if final_version != reader.version:
            raise AuditError("数据库在采集期间发生变化；不输出混合快照")
        summary["snapshot"] = {
            "data_version_before": reader.version,
            "data_version_after": final_version,
            "consistent": True,
        }
        return {
            "summary": summary,
            "alerts": final_rows,
            "attempts": jobs,
            "observations": observations,
            "candidates": candidates,
            "aggregation_audits": gates,
        }


def _observations(reader, experiment_id, runs, refs):
    fields = _fields(
        "observation_payload",
        {
            "experiment_id": "$.accumulation_scope.experiment_id",
            "behavior_strength": "$.signature.facets.behavior_strength[0]",
            "minimum_support": "$.aggregation_policy.minimum_support",
            "minimum_distinct_sources": "$.aggregation_policy.minimum_distinct_sources",
            "minimum_conclusive_support": "$.aggregation_policy.minimum_conclusive_support",
        },
    )
    select = f"""SELECT observation_id,aggregation_key,lineage_key,run_id,alert_id,profile_id,profile_version,feature_schema_version,
        created_at,{fields},json_array_length(observation_payload,'$.signature.facets.behavior_component_core') AS core_component_count
        FROM soc_memory_pattern_observations WHERE """
    rows = list(
        reader.pages(
            select
            + "json_extract(observation_payload,'$.accumulation_scope.experiment_id')=? ORDER BY observation_id",
            (experiment_id,),
        )
    )
    rows += list(
        reader.by_ids(select + "run_id IN ({ids}) ORDER BY observation_id", runs)
    )
    rows += list(
        reader.by_ids(
            select + "observation_id IN ({ids}) ORDER BY observation_id", refs
        )
    )
    return sorted(
        {r["observation_id"]: r for r in rows}.values(),
        key=lambda r: r["observation_id"],
    )


def _candidates(reader, experiment_id, runs, refs):
    fields = _fields(
        "candidate_payload",
        {
            "metadata_experiment_id": "$.metadata.experiment_id",
            "source_experiment_id": "$.source.metadata.experiment_id",
            "profile_id": "$.applicability.profile_id",
            "profile_version": "$.applicability.profile_version",
            "aggregation_key": "$.metadata.aggregation_key",
            "support_count_at_creation": "$.metadata.support_count_at_creation",
        },
    )
    select = f"""SELECT candidate_id,status,source_type AS candidate_source_type,source_run_id,source_alert_id,created_at,updated_at,{fields}
        FROM soc_memory_candidates WHERE """
    rows = list(
        reader.pages(
            select
            + "(json_extract(candidate_payload,'$.metadata.experiment_id')=? OR json_extract(candidate_payload,'$.source.metadata.experiment_id')=?) ORDER BY candidate_id",
            (experiment_id, experiment_id),
        )
    )
    rows += list(
        reader.by_ids(select + "source_run_id IN ({ids}) ORDER BY candidate_id", runs)
    )
    rows += list(
        reader.by_ids(select + "candidate_id IN ({ids}) ORDER BY candidate_id", refs)
    )
    return sorted(
        {r["candidate_id"]: r for r in rows}.values(), key=lambda r: r["candidate_id"]
    )


def _audits(reader, observations):
    fields = _fields(
        "record_payload",
        {
            key: "$.payload." + key
            for key in (
                "aggregation_key",
                "support_count",
                "distinct_source_count",
                "threshold_met",
                "quality_gate_passed",
                "quality_reason_codes",
                "candidate_created",
                "candidate_coverage",
                "candidate_id",
            )
        },
    )
    rows = list(
        reader.by_ids(
            f"""SELECT audit_id,target_id,occurred_at,{fields} FROM soc_mutation_audit_log
        WHERE operation='memory_pattern_observation.ingest' AND target_id IN ({{ids}}) ORDER BY occurred_at,audit_id""",
            [o["observation_id"] for o in observations],
        )
    )
    for row in rows:
        row["quality_reason_codes"] = _json(row["quality_reason_codes"])
    return rows


def _last_gates(audits):
    groups = defaultdict(list)
    for row in audits:
        if row["aggregation_key"] is not None:
            groups[row["aggregation_key"]].append(row)
    result = []
    for key, rows in sorted(groups.items()):
        last_time = max(_stamp(r["occurred_at"]) for r in rows)
        last = [row for row in rows if _stamp(row["occurred_at"]) == last_time]
        row = dict(last[0], time_tie=len(last) > 1)
        if len(last) > 1:
            for field in row:
                if field not in {"aggregation_key", "occurred_at", "time_tie"}:
                    row[field] = None
        result.append(row)
    return result


def _cell(value):
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = "" if value is None else str(value)
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
        else text
    )


def write_report(result, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = []
    summary = output_dir / "summary.json"
    with summary.open("x", encoding="utf-8") as stream:
        os.chmod(summary, 0o600)
        json.dump(result["summary"], stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    paths.append(summary)
    for name in (
        "alerts",
        "attempts",
        "observations",
        "candidates",
        "aggregation_audits",
    ):
        path = output_dir / (name + ".csv")
        rows = result[name]
        with path.open("x", newline="", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            fields = (
                sorted(set().union(*(row.keys() for row in rows)))
                if rows
                else ["no_records"]
            )
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(
                {key: _cell(row.get(key)) for key in fields} for row in rows
            )
        paths.append(path)
    manifest = {
        "schema_version": "soc.learning_candidate_audit_manifest.v1",
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
        },
    }
    path = output_dir / "manifest.json"
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="只读导出第一批经验候选诊断；不调用模型、不迁移、不修改数据库"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "deer-flow")
    parser.add_argument("--experiment", dest="experiment_id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--query-timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        result = collect(
            args.root / "backend/.deer-flow/data/soc_agent_dev.db",
            experiment_id=args.experiment_id,
            query_timeout=args.query_timeout,
        )
        output = args.output_dir or args.root / "backend/.deer-flow/soc-validation" / (
            "learning-candidate-audit-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        )
        path = write_report(result, output)
    except (AuditError, sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "exported",
                "summary": str(path),
                "experiment_id": result["summary"]["experiment"]["experiment_id"],
                "learning_members": result["summary"]["scope"]["learning_members"],
                "candidates_current": result["summary"]["candidates_current"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
