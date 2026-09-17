"""Prepare a private review artifact from a verified, read-only Workbench catalog."""

import csv
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from soc_agent.demo.corpus_batches import CorpusBatchCase, CorpusBatchPlan, build_corpus_batch_plan

INDEX_SCHEMA = "soc.corpus_workbench_index.v3"
STORE_SCHEMA = "soc.corpus_workbench_payload_store.v1"
WARNING_LABELS = {
    "fingerprint_missing": "静态分组尚无行为指纹",
    "direct_reuse_not_ready": "静态特征尚不足以直接复用",
    "event_time_unusable": "含无法解释时间的样本，已留在补充测试",
}
REASON_LABELS = {
    "group_early_samples": "组内较早样本，积累经验",
    "group_later_samples": "组内较晚样本，主要验证",
    "small_group": "同类有效时间样本不足6条",
    "singleton": "单例样本",
    "event_time_missing": "缺少事件时间",
    "event_time_invalid": "事件时间格式无效",
    "event_time_timezone_missing": "事件时间没有时区",
}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _verify_file(path: Path, identity: dict[str, Any]) -> None:
    if not path.is_file() or path.name != identity.get("file_name") or path.stat().st_size != identity.get("size_bytes"):
        raise ValueError(f"source file name/size mismatch: {path.name}")
    if _sha256(path) != identity.get("sha256"):
        raise ValueError(f"source hash mismatch: {path.name}")


def prepare_batch_preview(
    *,
    source_path: Path,
    index_path: Path,
    output_dir: Path,
    expected_profile: dict[str, Any],
    example_alert_ids: tuple[str, ...] = (),
) -> CorpusBatchPlan:
    """Only the new output directory is writable; no API, PKL loading or business DB."""
    if output_dir.exists():
        raise FileExistsError(f"preview already exists; choose a new directory: {output_dir}")
    source_path, index_path = source_path.resolve(), index_path.resolve()
    index_bytes = index_path.read_bytes()
    document = json.loads(index_bytes)
    if not isinstance(document, dict) or document.get("schema_version") != INDEX_SCHEMA:
        raise ValueError("unsupported workbench index schema")
    if document.get("memory_profile") != expected_profile:
        raise ValueError("index profile is incompatible with the current static grouping profile; do not overwrite it automatically")
    source, store = document.get("source"), document.get("payload_store")
    if not isinstance(source, dict) or not isinstance(store, dict) or store.get("schema_version") != STORE_SCHEMA:
        raise ValueError("missing source/payload store identity")
    name = store.get("file_name")
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("invalid payload store file name")
    store_path = index_path.parent / name
    if any(store_path.with_name(store_path.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("payload store has journal sidecars; use a frozen standalone corpus store")
    inputs = (source_path, index_path, store_path)
    signatures = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in inputs}
    _verify_file(source_path, source)
    _verify_file(store_path, store)
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != source.get("alert_count"):
        raise ValueError("index case count mismatch")
    if any(not isinstance(row, dict) for row in raw_cases):
        raise ValueError("index case must be an object")
    # Explicit allowlist keeps operational labels and raw evidence out of the manifest.
    cases = [CorpusBatchCase.model_validate({key: value for key, value in row.items() if key in CorpusBatchCase.model_fields}) for row in raw_cases]
    identity = {
        "source": source,
        "payload_store": store,
        "memory_profile": expected_profile,
        "index": {"file_name": index_path.name, "sha256": hashlib.sha256(index_bytes).hexdigest(), "size_bytes": len(index_bytes), "schema_version": INDEX_SCHEMA},
    }
    plan = build_corpus_batch_plan(cases, source_identity=identity)
    expected = {row.alert_id: (row.source_index, row.payload_hash) for row in cases}
    if set(example_alert_ids) - expected.keys():
        raise ValueError("an example alert ID is absent from this corpus")
    connection = sqlite3.connect(store_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if metadata.get("schema_version") != STORE_SCHEMA or metadata.get("source_sha256") != source["sha256"] or metadata.get("alert_count") != str(len(cases)):
            raise ValueError("payload store metadata does not match index/source")
        rows = connection.execute("SELECT alert_id, source_index, payload_hash FROM payloads").fetchall()
        actual = {str(alert_id): (source_index, payload_hash) for alert_id, source_index, payload_hash in rows}
        if len(rows) != len(expected) or actual != expected:
            raise ValueError("payload identity inventory does not match index")
    finally:
        connection.close()
    if any((p.stat().st_size, p.stat().st_mtime_ns) != signatures[p] for p in inputs) or index_path.read_bytes() != index_bytes:
        raise ValueError("source artifacts changed during preview preparation; retry with a stable corpus")
    _write_preview(plan, output_dir, example_alert_ids)
    return plan


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    # Spreadsheet viewers must not execute untrusted rule names as formulas.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: _cell(value) for key, value in row.items()} for row in rows)


def _write_preview(plan: CorpusBatchPlan, output: Path, example_ids: tuple[str, ...]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".batch-preview-", dir=output.parent) as temporary:
        directory = Path(temporary)
        (directory / "manifest.json").write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (directory / "preview.md").write_text(render_preview(plan, example_ids), encoding="utf-8")
        _csv(
            directory / "members.csv",
            [
                {
                    "告警ID": m.alert_id,
                    "同类组": m.group_id,
                    "批次": "第一批" if m.batch == "learning" else "第二批",
                    "用途": "沉淀经验" if m.batch == "learning" else "主要验证" if m.validation_tier == "main" else "补充测试",
                    "划分依据": REASON_LABELS[m.reason],
                    "事件时间UTC": m.event_time_utc.isoformat() if m.event_time_utc else "",
                    "原始时间": m.observed_at,
                    "组内顺序": m.position_in_group,
                    "规则编码": m.rule_code,
                    "规则名称": m.rule_name,
                    "源行号": m.source_index,
                    "载荷Hash": m.payload_hash,
                }
                for m in plan.members
            ],
        )
        _csv(
            directory / "groups.csv",
            [
                {
                    "同类组": g.group_id,
                    "规则编码": ", ".join(g.rule_codes),
                    "规则名称": ", ".join(g.rule_names),
                    "核心行为": " | ".join(g.behavior_components),
                    "总数": g.total,
                    "第一批": g.learning_count,
                    "第二批主要验证": g.validation_main_count,
                    "第二批补充测试": g.validation_supplementary_count,
                    "第一批最后告警": g.learning_last_id,
                    "第一批最后时间UTC": g.learning_last_at,
                    "第二批首条告警": g.validation_first_id,
                    "第二批首条时间UTC": g.validation_first_at,
                    "普通窗口最多可积累条数（对照值，非本轮门槛）": g.ordinary_window_max_count,
                    "边界时间相同": g.boundary_same_time,
                    "注意事项": "；".join(WARNING_LABELS[w] for w in g.warnings),
                }
                for g in plan.groups
            ],
        )
        for path in directory.iterdir():
            path.chmod(0o600)
        if output.exists():
            raise FileExistsError(f"preview directory appeared while preparing: {output}")
        directory.rename(output)


def render_preview(plan: CorpusBatchPlan, example_ids: tuple[str, ...] = ()) -> str:
    groups = sorted(plan.groups, key=lambda g: (-g.total, g.group_id))
    warning_counts = Counter(w for g in groups if g.learning_count for w in g.warnings)
    counts = plan.counts
    lines = [
        "# 两批样本划分预览",
        "",
        "状态：待你确认，尚未启用。本预览没有运行告警，也没有生成或修改经验。",
        "",
        f"名单标识：`{plan.plan_id}`",
        "",
        "## 总量",
        "",
        "| 用途 | 告警数 | 同类组数 |",
        "|---|---:|---:|",
        f"| 第一批：沉淀经验 | {counts['learning']:,} | {sum(g.learning_count > 0 for g in groups):,} |",
        f"| 第二批：主要验证 | {counts['validation_main']:,} | {sum(g.validation_main_count > 0 for g in groups):,} |",
        f"| 第二批：补充测试 | {counts['validation_supplementary']:,} | {sum(g.validation_supplementary_count > 0 for g in groups):,} |",
        f"| 全部（不删数据） | {counts['total']:,} | {len(groups):,} |",
        "",
        "组内按 UTC 事件时间、告警 ID、载荷 Hash 排序；至少6条有效时间样本才分两批。",
        "第一批取约70%，至少5条、每组最多10条，且为第二批留至少1条；其余组内样本全部用于主要验证。",
        "2～5条的小组、单例和时间不明样本保留为补充测试，带独立标签，不计入主要验证分母。",
        "同一时间可按稳定 ID 分到两侧；这是同场景经验复用测试，不是严格全局未来时间外推。",
        "",
        "## 积累方式",
        "",
        "已确认采用“本轮第一批统一积累”：同一实验、同一实际行为模式的有效样本跨日期积累。",
        "真实事件时间保持不变；普通运行仍使用现有30天窗口，第二批不混入第一批积累。",
        "这项聚合机制将在批跑接入时实施；当前仅保存计划，未改变 Runtime 或开启实验执行。",
        "静态同类组不是聚合依据；若10条样本运行后细分为多个模式，分别计数，不保证每个模式都满5条。",
        "",
        "## 需要注意",
        "",
        "静态分组版本与当前静态分组代码相容；这不是运行后的语义核对结果。",
        "内网实际运行后可能细分为多个模式，下面的组数不等于将产生的经验数量。",
        "弱特征/缺指纹不会被偷偷从主要验证分母剔除；有效结果、独立样本及可复用范围等质量条件仍保留。",
        "外网不调用模型；真实批跑、经验审核和第二批验证全部留在内网 Mac DEV。",
        "",
    ]
    lines.extend(f"- {WARNING_LABELS[key]}：{count} 个积累组。" for key, count in sorted(warning_counts.items()))
    lines.append(f"- 两批分界处时间相同：{sum(g.boundary_same_time for g in groups)} 个组。")
    members_by_group: dict[str, list[Any]] = {}
    by_id = {m.alert_id: m for m in plan.members}
    for m in plan.members:
        members_by_group.setdefault(m.group_id, []).append(m)
    selected = list(dict.fromkeys(by_id[alert_id].group_id for alert_id in example_ids))
    if not selected:
        selected = [g.group_id for g in groups[:3]]
    for predicate in (lambda g: g.total == 6, lambda g: g.total == 10, lambda g: 2 <= g.total <= 5, lambda g: g.total == 1):
        group = next((g for g in groups if predicate(g) and g.group_id not in selected), None)
        if group:
            selected.append(group.group_id)
    lines.extend(["", "## 实际分组例子", ""])
    for group_id in selected:
        g = next(g for g in groups if g.group_id == group_id)
        lines.extend(
            [
                f"### {' / '.join(g.rule_names) or '未命名规则'} · {g.group_id}",
                "",
                f"合计 {g.total} 条：第一批 {g.learning_count}；第二批主要验证 {g.validation_main_count}；补充测试 {g.validation_supplementary_count}。",
                "",
                "行为：" + ("；".join(g.behavior_components) or "静态分组未提取到行为指纹"),
                "",
            ]
        )
        for label, predicate in (("第一批", lambda m: m.batch == "learning"), ("第二批主要验证", lambda m: m.validation_tier == "main"), ("补充测试", lambda m: m.validation_tier == "supplementary")):
            rows = [m for m in members_by_group[group_id] if predicate(m)]
            if rows:
                ids = ", ".join(m.alert_id for m in rows[:5]) + (" ..." if len(rows) > 5 else "")
                lines.append(f"- {label}：{ids}；时间 {rows[0].event_time_utc} 至 {rows[-1].event_time_utc}。")
        if g.learning_last_id:
            lines.append(f"- 分界：{g.learning_last_id} ({g.learning_last_at}) -> {g.validation_first_id} ({g.validation_first_at})。")
        for warning in g.warnings:
            lines.append(f"- 注意：{WARNING_LABELS[warning]}。")
        lines.append("")
    lines.extend(
        [
            "## 文件与下一步",
            "",
            "- `members.csv`：每条告警的归属、时间和划分原因。",
            "- `groups.csv`：全部同类组、数量、分界和注意事项，可筛选排序。",
            "- `manifest.json`：完整可核对名单，绑定 PKL、索引、载荷库 Hash 与静态 Profile；无原始日志或运营标签。",
            "",
            "当前不接入页面、不创建运行任务；确认分批规则和例子后再实施页面切换。",
            "",
        ]
    )
    return "\n".join(lines)
