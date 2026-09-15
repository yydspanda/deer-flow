"""Read-only presentation of saved semantic review, never a second normalizer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from soc_agent.contracts import AnalysisRun


class NormalizationReviewFieldView(BaseModel):
    field: str
    label: str
    before: Any = None
    after: Any = None


class NormalizationReviewChangeView(BaseModel):
    target: str
    label: str
    fields: list[NormalizationReviewFieldView]
    source_id: str
    source_path: str
    source_quote: str
    reason: str


class NormalizationReviewView(BaseModel):
    mode: Literal["shadow", "apply"]
    status: str
    status_label: str
    effect_label: str
    after_label: str
    model_name: str
    total_tokens: int | None = None
    duration_ms: float | None = None
    source_count: int = 0
    changes: list[NormalizationReviewChangeView] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)


_LABELS = {
    "file_name": "文件名",
    "file_path": "文件路径",
    "process_name": "进程名",
    "process_path": "进程路径",
    "process_id": "进程 ID",
    "command_line": "命令行",
    "parent_process_name": "父进程",
    "parent_command_line": "父进程命令",
    "parent_process_id": "父进程 ID",
    "username": "账号",
    "host_name": "主机名",
    "host_id": "主机标识",
    "ip_addresses": "主机 IP",
    "ip_address": "IP 地址",
    "source_ip": "连接源 IP",
    "destination_ip": "连接目标 IP",
    "src_port": "源端口",
    "dst_port": "目标端口",
    "protocol": "协议",
    "application_protocol": "应用协议",
    "domain": "域名",
    "url": "URL",
    "method": "HTTP 方法",
    "status_code": "HTTP 状态码",
    "path": "请求路径",
    "host": "请求主机",
    "user_agent": "客户端标识",
    "name": "名称",
    "category": "检测类型",
    "detector_id": "检测标识",
    "reported_result": "上游检测结果",
    "action": "上游执行动作",
    "subject_refs": "关联对象",
    "subject_ref": "关联对象",
    "kind": "对象或事件类型",
    "value": "记录值",
    "meaning": "含义",
    "container_name": "容器名",
    "container_id": "容器标识",
    "image_name": "镜像名称",
    "namespace": "命名空间",
    "pod_name": "Pod 名称",
    "cluster_name": "集群名",
    "md5": "MD5",
    "sha1": "SHA1",
    "sha256": "SHA256",
    "exists": "是否存在",
}
_KINDS = {"process": "进程", "file": "文件", "network": "网络连接", "http": "HTTP", "host": "主机", "user": "账号", "detections": "检测事件", "context_observations": "上下文对象", "supplementary_facts": "补充事实"}
_METADATA = {"observation_id", "evidence_path", "event_scope_id", "relation"}
_STATUS = {"applied": "核对完成", "shadow": "核对完成", "unchanged": "无需调整", "partial": "核对完成", "failed": "核对失败", "skipped": "未执行"}


def _leaves(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        return {k: v for name, child in value.items() if name not in _METADATA for k, v in _leaves(child, f"{path}.{name}".strip(".")).items()}
    if isinstance(value, list) and any(isinstance(item, dict) for item in value):
        return {k: v for index, child in enumerate(value) for k, v in _leaves(child, f"{path}[{index}]").items()}
    return {path: value}


def build_normalization_review_view(run: AnalysisRun) -> NormalizationReviewView | None:
    report = run.normalization_assistance
    if report is None:
        return None
    effect = "仅对比，未用于本次研判或经验匹配" if report.mode == "shadow" else "已合入标准告警，供后续研判与经验条件构建使用"
    if report.status == "failed":
        effect = "核对失败，沿用 Adapter 结果"
    elif report.status == "skipped":
        effect = "本次未执行语义核对"
    elif report.mode == "apply" and not report.changes and not report.observation_changes:
        effect = "未修改标准告警，沿用 Adapter 结果"
    elif report.mode == "apply":
        adopted = sum(change.canonical_status == "applied" for change in [*report.changes, *report.observation_changes])
        effect = f"核对完成，已采用 {adopted} 项补充；供后续研判与经验条件构建使用"
    usage = report.metadata.get("usage", {})
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    view = NormalizationReviewView(
        mode=report.mode,
        status=report.status,
        status_label=_STATUS[report.status],
        effect_label=effect,
        after_label="模型建议" if report.mode == "shadow" else "实际采用",
        model_name=report.model_name,
        total_tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        duration_ms=sum(step.duration_ms or 0 for step in run.steps if step.step_name in {"build_normalization_input", "normalization_assist", "apply_normalization"}) or None,
        issues=list(report.issues),
    )
    for change in [*report.changes, *report.observation_changes]:
        before, after = _leaves(change.before), _leaves(change.after)
        fields = []
        for field, value in after.items():
            if before.get(field) == value:
                continue
            key = field.rsplit(".", 1)[-1] or change.target.rsplit(".", 1)[-1]
            label = _LABELS.get(key, key)
            if field.startswith("nodes["):
                label = f"{field.split('.', 1)[0]} · {label}"
            fields.append(NormalizationReviewFieldView(field=field or key, label=label, before=before.get(field), after=value))
        section = change.target.split(".")[1].split("[")[0]
        view.changes.append(
            NormalizationReviewChangeView(
                target=change.target,
                label=_KINDS.get(section, "事实"),
                fields=fields,
                source_id=getattr(change, "source_id", "L0"),
                source_path=getattr(change, "source_path", None) or (run.normalization_assist_request.source_path if run.normalization_assist_request else "") or "",
                source_quote=change.source_quote,
                reason=change.reason,
            )
        )
    request = run.normalization_assist_request
    if request:
        view.source_count = len(request.sources) or (1 if request.source_text else 0)
        if request.omitted_source_count:
            view.coverage_notes.append(f"另有 {request.omitted_source_count} 条消息未纳入本次核对。")
        if request.input_truncated or any(s.truncated for s in request.sources):
            view.coverage_notes.append("部分日志超过核对输入预算，尾部未核对。")
        if request.omitted_object_count:
            view.coverage_notes.append(f"已有对象目录省略 {request.omitted_object_count} 项。")
        if request.deduplicated_source_count:
            view.coverage_notes.append(f"{request.deduplicated_source_count} 条重复消息共用一次核对。")
    return view
