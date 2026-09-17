"""Offline selection from a fixed report, never from mutable latest-alert state."""

import hashlib
import json
from pathlib import Path

from soc_agent.contracts.corpus_experiments import CorpusRetestPlan, CorpusRetestProvenance, CorpusRoundSelection
from soc_agent.demo.corpus_round_comparison import comparison_results_hash
from soc_agent.utils.hashing import stable_hash


def build_retest_plan(report_path: Path, *, failed=False, used_memory_ids=(), group_ids=(), rule_codes=(), alert_ids=(), all_rows=False) -> CorpusRetestPlan:
    filters = {"failed": failed, "used_memory_ids": sorted(set(used_memory_ids)), "group_ids": sorted(set(group_ids)), "rule_codes": sorted(set(rule_codes)), "alert_ids": sorted(set(alert_ids)), "all_rows": all_rows}
    narrowed = any(filters[key] for key in filters if key != "all_rows")
    if not narrowed and not all_rows:
        raise ValueError("请提供复测筛选条件，或用 --all 明确选择旧报告的全部成员")
    if narrowed and all_rows:
        raise ValueError("--all 不能与筛选条件同时使用")
    path = report_path / "report.json" if report_path.is_dir() else report_path
    raw = path.read_bytes()
    report = json.loads(raw)
    if not isinstance(report, dict) or report.get("schema_version") != "soc.corpus_round_report.v1":
        raise ValueError("需要 export 生成的固定轮次 report.json")
    round_ = report.get("round", {})
    if not round_.get("experiment_id") or not round_.get("round_id") or not report.get("source_identity") or not isinstance(report.get("rows"), list):
        raise ValueError("旧报告缺少实验、轮次、数据身份或告警成员")
    old_selection = CorpusRoundSelection.model_validate(round_.get("selection"))
    rows = report["rows"]
    seen = set()
    for row in rows:
        alert_id = row.get("alert_id") if isinstance(row, dict) else None
        if not isinstance(alert_id, str) or not alert_id:
            raise ValueError("旧报告包含无效 alert_id")
        if alert_id in seen:
            raise ValueError("旧报告包含重复 alert_id，不能选择模糊的历史运行")
        seen.add(alert_id)
    if set(alert_ids) - seen:
        raise ValueError("指定告警不在旧报告中；边界外样本请另建轮次选取，不能伪造旧运行关系")
    selected = []
    for row in rows:
        if failed and row.get("status") != "failed":
            continue
        if used_memory_ids and not set(used_memory_ids).intersection(use["memory_id"] for use in row.get("summary", {}).get("memory_uses", [])):
            continue
        if group_ids and row.get("group_id") not in group_ids:
            continue
        if rule_codes and row.get("rule_code") not in rule_codes:
            continue
        if alert_ids and row["alert_id"] not in alert_ids:
            continue
        selected.append(row)
    if not selected:
        raise ValueError("没有符合条件的复测告警；Memory 筛选只认实际使用记录，不认经验快照中的库存")
    selected.sort(key=lambda row: row["alert_id"])
    selection = CorpusRoundSelection(batch=old_selection.batch, scope=old_selection.scope, alert_ids=[row["alert_id"] for row in selected])
    provenance = CorpusRetestProvenance(
        parent_round_id=round_["round_id"],
        report_sha256=hashlib.sha256(raw).hexdigest(),
        selection_hash=stable_hash(selection.model_dump(mode="json")),
        source_identity=report["source_identity"],
        selected_results_hash=comparison_results_hash(selected),
    )
    value = {
        "schema_version": "soc.corpus_retest_plan.v1",
        "experiment_id": round_["experiment_id"],
        "provenance": provenance.model_dump(mode="json"),
        "selection": selection.model_dump(mode="json"),
        "previous_run_ids": {row["alert_id"]: row.get("run_id") for row in selected},
        "validation_tiers": {row["alert_id"]: row.get("validation_tier") for row in selected},
        "filters": filters,
    }
    return CorpusRetestPlan.model_validate({**value, "plan_hash": stable_hash(value)})


def read_retest_plan(path: Path) -> CorpusRetestPlan:
    plan = CorpusRetestPlan.model_validate_json(path.read_text(encoding="utf-8"))
    if stable_hash(plan.model_dump(mode="json", exclude={"plan_hash"})) != plan.plan_hash or stable_hash(plan.selection.model_dump(mode="json")) != plan.provenance.selection_hash:
        raise ValueError("复测名单校验失败，请用 retest-plan 重新生成，不要手工改动固定名单")
    if not plan.selection.alert_ids or set(plan.selection.alert_ids) != set(plan.previous_run_ids) or set(plan.previous_run_ids) != set(plan.validation_tiers):
        raise ValueError("复测名单与旧运行记录不一致")
    return plan
