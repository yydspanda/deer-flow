"""Generic SOC security scenario recognition helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import (
    AlertInput,
    AnalysisRun,
    SocDomainFinding,
    SocDomainFindingDisposition,
    SocDomainFindingSeverity,
    SocDomainName,
    SocDomainTriageRequest,
    SocEvidenceProfile,
    SocFindingConclusion,
    SocSkillContext,
)
from soc_agent.domain.evidence import evidence_is_mocked, successful_evidence, successful_evidence_routes
from soc_agent.normalizers import normalize_alert_payload

SCENARIO_TAXONOMY_VERSION = "soc.scenario_taxonomy.v1"

_SCENARIO_RULES: tuple[dict[str, Any], ...] = (
    {
        "key": "execution.reverse_shell",
        "name": "疑似反弹 shell",
        "family": "execution",
        "severity": SocDomainFindingSeverity.HIGH,
        "queue": "endpoint_review",
        "keywords": (
            "reverse shell",
            "revshell",
            "反弹shell",
            "反弹 shell",
            "反连",
            "bash -i",
            "/dev/tcp",
            "nc -e",
            "ncat -e",
            "socat exec",
            "powershell -nop",
        ),
        "required_routes": ("asset.locate", "threat_intel.ip_reputation.lookup"),
        "checklist": (
            "确认父进程是否为 Web 服务、脚本解释器、计划任务或异常启动器。",
            "确认外联 IP 是否为授权运维跳板、业务依赖或威胁情报命中对象。",
            "确认执行账号是否为业务账号、运维账号或异常账号。",
            "检查同主机近期是否还有提权、持久化或横向移动迹象。",
        ),
    },
    {
        "key": "web.webshell",
        "name": "疑似 WebShell",
        "family": "web",
        "severity": SocDomainFindingSeverity.HIGH,
        "queue": "web_review",
        "keywords": (
            "webshell",
            "web shell",
            "一句话",
            "蚁剑",
            "冰蝎",
            "哥斯拉",
            "菜刀",
            "eval(",
            "assert(",
            "cmd.jsp",
            "shell.jsp",
            "cmd.php",
            "shell.php",
        ),
        "required_routes": ("asset.locate", "security_tag.lookup"),
        "checklist": (
            "确认请求路径、上传文件、脚本后缀和 Web 目录是否异常。",
            "检查同资产近期是否存在命令执行、文件写入或异常外联。",
            "核对该路径是否属于业务发布、扫描器测试或授权演练。",
        ),
    },
    {
        "key": "lateral_movement",
        "name": "疑似横向移动",
        "family": "endpoint",
        "severity": SocDomainFindingSeverity.HIGH,
        "queue": "endpoint_review",
        "keywords": (
            "横向移动",
            "lateral movement",
            "psexec",
            "wmic",
            "winrm",
            "remote registry",
            "远程注册表",
            "smb",
            "rdp",
            "admin$",
            "ipc$",
        ),
        "required_routes": ("asset.locate", "security_tag.lookup"),
        "checklist": (
            "确认源主机、目标主机、账号和协议是否符合正常运维路径。",
            "检查同账号是否在短时间内访问多台主机。",
            "结合历史相似预警判断是否为已知运维工具或真实横向移动。",
        ),
    },
    {
        "key": "execution.suspicious_command",
        "name": "可疑命令/代码执行",
        "family": "execution",
        "severity": SocDomainFindingSeverity.MEDIUM,
        "queue": "endpoint_review",
        "keywords": (
            "命令执行",
            "代码执行",
            "command execution",
            "remote code execution",
            "rce",
            "curl http",
            "wget http",
            "powershell",
            "cmd.exe /c",
            "certutil",
            "bitsadmin",
            "process_execution",
        ),
        "required_routes": ("asset.locate", "security_tag.lookup"),
        "checklist": (
            "确认命令行、父进程、执行用户和执行时间是否符合业务行为。",
            "检查命令是否下载脚本、执行内存载荷或连接未知地址。",
            "结合相似告警处置结论判断是否为扫描器、批处理或授权运维。",
        ),
    },
    {
        "key": "network.malicious_outbound",
        "name": "疑似恶意外联",
        "family": "network",
        "severity": SocDomainFindingSeverity.HIGH,
        "queue": "network_review",
        "keywords": (
            "恶意外联",
            "外联",
            "c2",
            "c&c",
            "command and control",
            "beacon",
            "回连",
            "dns tunnel",
            "dns隧道",
        ),
        "required_routes": ("threat_intel.ip_reputation.lookup", "asset.locate"),
        "checklist": (
            "确认外联方向、源资产、目标 IP/域名和协议端口。",
            "核对目标是否命中威胁情报、授权运维跳板或业务依赖。",
            "查看历史相似预警是否集中在同资产、同目标或同 rule。",
        ),
    },
    {
        "key": "privilege_escalation",
        "name": "疑似提权",
        "family": "endpoint",
        "severity": SocDomainFindingSeverity.HIGH,
        "queue": "endpoint_review",
        "keywords": (
            "提权",
            "privilege escalation",
            "uac bypass",
            "sudo",
            "setuid",
            "token impersonation",
            "高权限",
        ),
        "required_routes": ("asset.locate", "security_tag.lookup"),
        "checklist": (
            "确认低权限到高权限的进程链、账号变化和触发时间。",
            "核对是否存在授权变更、补丁安装、运维脚本或漏洞利用迹象。",
            "检查同主机是否伴随持久化、凭证访问或横向移动。",
        ),
    },
    {
        "key": "credential_abuse",
        "name": "疑似凭证滥用",
        "family": "identity",
        "severity": SocDomainFindingSeverity.HIGH,
        "queue": "identity_review",
        "keywords": (
            "凭证",
            "credential",
            "mimikatz",
            "lsass",
            "dump",
            "hashdump",
            "暴力破解",
            "异常登录",
            "credential_access",
        ),
        "required_routes": ("asset.locate", "security_tag.lookup"),
        "checklist": (
            "确认账号、来源、目标资产、登录时间和认证方式是否异常。",
            "检查是否存在 LSASS 访问、凭证导出、暴力破解或多点登录。",
            "结合用户历史行为、相似预警和外部处置理由判断是否为误报。",
        ),
    },
)


def scenario_taxonomy_snapshot() -> list[dict[str, Any]]:
    """Return a stable, replay-diff friendly view of deterministic scenario rules."""

    return [
        {
            "key": str(rule["key"]),
            "name": str(rule["name"]),
            "family": str(rule["family"]),
            "severity": rule["severity"].value if isinstance(rule["severity"], SocDomainFindingSeverity) else str(rule["severity"]),
            "review_queue": str(rule["queue"]),
            "required_routes": list(rule["required_routes"]),
            "keyword_count": len(rule["keywords"]),
        }
        for rule in _SCENARIO_RULES
    ]


def scenario_taxonomy_keys() -> list[str]:
    """Return deterministic scenario keys in taxonomy order."""

    return [item["key"] for item in scenario_taxonomy_snapshot()]


def scenario_findings(request: SocDomainTriageRequest, domain: SocDomainName) -> list[SocDomainFinding]:
    alert = _normalized_alert(request.run)
    corpus = _scenario_corpus(request.run, alert)
    if not corpus:
        return []

    vendor_scenarios = _vendor_scenarios(alert)
    action_routes = _available_action_routes(request)
    findings: list[SocDomainFinding] = []
    for rule in _SCENARIO_RULES:
        matched_terms = _matched_terms(corpus, rule["keywords"])
        if not matched_terms:
            continue
        scenario_key = str(rule["key"])
        scenario_name = str(rule["name"])
        required_routes = tuple(str(item) for item in rule["required_routes"])
        gaps = _scenario_gaps(required_routes, action_routes)
        used_sources = _used_evidence_sources(request)
        confidence = _scenario_confidence(matched_terms, request, required_routes, action_routes)
        severity = rule["severity"]
        if not isinstance(severity, SocDomainFindingSeverity):
            severity = SocDomainFindingSeverity.MEDIUM
        finding = SocDomainFinding(
            domain=domain,
            scenario_key=scenario_key,
            scenario_name=scenario_name,
            vendor_scenarios=vendor_scenarios,
            title=scenario_name,
            summary=_scenario_summary(scenario_name, matched_terms, request),
            severity=severity,
            disposition=SocDomainFindingDisposition.SUSPICIOUS,
            confidence=confidence,
            evidence_profile=evidence_profile_for_request(
                request,
                used_sources=used_sources,
                gaps=gaps,
            ),
            current_conclusion=finding_conclusion(
                _scenario_conclusion_summary(scenario_name, confidence, gaps, request),
                risk_level=severity,
                confidence=confidence,
                recommended_action="manual_review",
                recommended_queue=str(rule["queue"]),
                rationale=[
                    "场景识别来自 raw/canonical alert、历史相似预警、外部反馈、confirmed memory 和可用只读证据的证据融合。",
                    "该 finding 不是最终 operational verdict；高风险动作仍必须走审批。",
                ],
            ),
            evidence_refs=_scenario_evidence_refs(request),
            skill_names=_skill_names(request.skill_context),
            recommendations=[
                "结合历史相似预警、外部处置理由和 confirmed memory 复核当前场景。",
                "工具证据缺失时不要中止研判；降低置信度并明确证据缺口。",
                "如需阻断、隔离或封禁，生成高风险 action proposal 并走审批。",
            ],
            limitations=gaps,
            human_checklist=list(rule["checklist"]),
            metadata={
                "scenario_family": rule["family"],
                "matched_terms": matched_terms,
                "available_action_routes": action_routes,
                "vendor_scenarios": vendor_scenarios,
            },
        )
        findings.append(finding)
    if findings:
        return findings[:3]
    if vendor_scenarios:
        return [_unmapped_vendor_scenario_finding(request, domain, vendor_scenarios, action_routes)]
    return []


def finding_conclusion(
    summary: str,
    *,
    risk_level: SocDomainFindingSeverity,
    confidence: float,
    recommended_action: str,
    recommended_queue: str | None,
    rationale: list[str],
) -> SocFindingConclusion:
    return SocFindingConclusion(
        summary=summary,
        risk_level=risk_level,
        certainty=_certainty_for_confidence(confidence),
        recommended_action=recommended_action,
        recommended_queue=recommended_queue,
        automation_allowed=False,
        rationale=rationale,
    )


def evidence_profile_for_request(
    request: SocDomainTriageRequest,
    *,
    used_sources: list[str],
    gaps: list[str],
) -> SocEvidenceProfile:
    metadata = request.metadata
    similar_alert_count = _similar_alert_count(request)
    correlation_match_count = _correlation_match_count(request)
    available_routes = _available_action_routes(request)
    successful_mock_evidence = [item for item in successful_evidence(request.investigation_evidence, include_mocked=True) if evidence_is_mocked(item)]
    sources = {
        "raw_log": "available" if request.run.input_payload else "missing",
        "similar_alerts": _count_status(similar_alert_count),
        "correlation": _count_status(correlation_match_count),
        "external_feedback": _count_status(metadata.get("external_disposition_count")),
        "confirmed_memory": _count_status(metadata.get("relevant_memory_count")),
        "memory_candidates": _count_status(metadata.get("memory_candidate_count")),
        "read_only_action_evidence": "available" if successful_evidence(request.investigation_evidence) else "missing",
        "mock_action_evidence": "available" if successful_mock_evidence else "missing",
        "threat_intel": "available" if "threat_intel.ip_reputation.lookup" in available_routes else "not_available_in_context",
        "asset_lookup": "available" if "asset.lookup" in available_routes or "asset.locate" in available_routes else "not_available_in_context",
    }
    used = [item for item in _dedupe_strs(used_sources) if item in sources or item == "raw_log"]
    return SocEvidenceProfile(
        sources=sources,
        used_sources=used,
        gaps=_dedupe_strs(gaps),
        notes=[
            "History, external feedback, confirmed memory, raw facts, and available tool evidence are all regular evidence inputs.",
            "Missing tool evidence lowers certainty but must not block the current conclusion.",
        ],
    )


def _certainty_for_confidence(confidence: float) -> str:
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.72:
        return "medium_high"
    if confidence >= 0.55:
        return "medium"
    if confidence >= 0.35:
        return "medium_low"
    return "low"


def _scenario_confidence(
    matched_terms: list[str],
    request: SocDomainTriageRequest,
    required_routes: tuple[str, ...],
    action_routes: list[str],
) -> float:
    score = 0.48 + min(len(matched_terms), 4) * 0.07
    if _similar_alert_count(request):
        score += 0.05
    if request.metadata.get("external_disposition_count", 0):
        score += 0.04
    if request.metadata.get("relevant_memory_count", 0):
        score += 0.08
    if any(route in action_routes for route in required_routes):
        score += 0.08
    if not successful_evidence(request.investigation_evidence):
        score -= 0.04
    return min(max(round(score, 2), 0.3), 0.88)


def _scenario_summary(
    scenario_name: str,
    matched_terms: list[str],
    request: SocDomainTriageRequest,
) -> str:
    sources = _used_evidence_sources(request)
    return f"Detected {scenario_name} signals from evidence fusion. Matched terms: {', '.join(matched_terms[:5])}. Evidence inputs used: {', '.join(sources) if sources else 'raw_log'}."


def _scenario_conclusion_summary(
    scenario_name: str,
    confidence: float,
    gaps: list[str],
    request: SocDomainTriageRequest,
) -> str:
    history_bits = []
    similar_alert_count = _similar_alert_count(request)
    if similar_alert_count:
        history_bits.append(f"{similar_alert_count} 条历史相似预警")
    if request.metadata.get("external_disposition_count", 0):
        history_bits.append(f"{request.metadata['external_disposition_count']} 条外部处置反馈")
    if request.metadata.get("relevant_memory_count", 0):
        history_bits.append(f"{request.metadata['relevant_memory_count']} 条 confirmed memory")
    history = "，".join(history_bits) if history_bits else "当前上下文未命中历史/反馈/memory"
    gap_text = "；证据缺口：" + "、".join(gaps) if gaps else "；关键只读证据已在当前上下文中出现"
    return f"当前结论：{scenario_name}，置信度 {confidence:.2f}；{history}{gap_text}。建议给出人工复核路径，不允许自动执行高风险处置。"


def _scenario_gaps(required_routes: tuple[str, ...], action_routes: list[str]) -> list[str]:
    gaps = []
    for route in required_routes:
        if route not in action_routes:
            gaps.append(f"current context has no {route} evidence")
    return gaps


def _unmapped_vendor_scenario_finding(
    request: SocDomainTriageRequest,
    domain: SocDomainName,
    vendor_scenarios: list[str],
    action_routes: list[str],
) -> SocDomainFinding:
    gaps = [
        "No internal scenario taxonomy rule matched vendor-provided scenario hints.",
        "No scenario-specific tool plan is available yet.",
    ]
    confidence = _unmapped_vendor_confidence(request)
    scenario_name = f"未映射厂商场景：{vendor_scenarios[0]}"
    return SocDomainFinding(
        domain=domain,
        scenario_key="vendor.unmapped",
        scenario_name=scenario_name,
        vendor_scenarios=vendor_scenarios,
        title=scenario_name,
        summary=(f"Vendor or upstream alert context provided scenario hints that are not yet mapped to the internal SOC scenario taxonomy. Vendor hints: {', '.join(vendor_scenarios[:5])}."),
        severity=SocDomainFindingSeverity.MEDIUM,
        disposition=SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE,
        confidence=confidence,
        evidence_profile=evidence_profile_for_request(
            request,
            used_sources=_used_evidence_sources(request),
            gaps=gaps,
        ),
        current_conclusion=finding_conclusion(
            _unmapped_vendor_conclusion_summary(vendor_scenarios, confidence, gaps, request),
            risk_level=SocDomainFindingSeverity.MEDIUM,
            confidence=confidence,
            recommended_action="manual_review",
            recommended_queue=_default_review_queue_for_domain(domain),
            rationale=[
                "上游场景提示只能作为候选线索；未映射到内部 taxonomy 前不能作为最终场景结论。",
                "仍需结合 raw/canonical alert、历史相似预警、外部反馈、confirmed memory 和只读工具证据给出当前研判。",
            ],
        ),
        evidence_refs=_scenario_evidence_refs(request),
        skill_names=_skill_names(request.skill_context),
        recommendations=[
            "先按当前 domain handler 的基础 finding 完成人工复核，不因场景未映射而中止研判。",
            "核对上游场景名是否只是厂商分类、规则分类或真实攻击场景。",
            "若该场景反复出现且有稳定处置经验，将其沉淀为 capability card、eval fixture 或 pending memory candidate。",
        ],
        limitations=gaps,
        human_checklist=[
            "查看 raw/canonical alert，确认上游场景提示是否有直接证据支撑。",
            "检索同 tenant/source/rule/entity 的历史相似预警和外部处置理由。",
            "检查当前可用只读证据是否能支持或反驳该厂商场景。",
            "判断是否需要新增内部 scenario taxonomy、domain skill 或 capability card。",
        ],
        metadata={
            "scenario_family": "vendor_unmapped",
            "available_action_routes": action_routes,
            "vendor_scenarios": vendor_scenarios,
            "taxonomy_candidate": True,
        },
    )


def _unmapped_vendor_confidence(request: SocDomainTriageRequest) -> float:
    score = 0.38
    if request.run.input_payload:
        score += 0.04
    if _similar_alert_count(request):
        score += 0.04
    if request.metadata.get("external_disposition_count", 0):
        score += 0.04
    if request.metadata.get("relevant_memory_count", 0):
        score += 0.06
    if successful_evidence(request.investigation_evidence):
        score += 0.04
    return min(max(round(score, 2), 0.32), 0.62)


def _unmapped_vendor_conclusion_summary(
    vendor_scenarios: list[str],
    confidence: float,
    gaps: list[str],
    request: SocDomainTriageRequest,
) -> str:
    history_bits = []
    similar_alert_count = _similar_alert_count(request)
    if similar_alert_count:
        history_bits.append(f"{similar_alert_count} 条历史相似预警")
    if request.metadata.get("external_disposition_count", 0):
        history_bits.append(f"{request.metadata['external_disposition_count']} 条外部处置反馈")
    if request.metadata.get("relevant_memory_count", 0):
        history_bits.append(f"{request.metadata['relevant_memory_count']} 条 confirmed memory")
    history = "，".join(history_bits) if history_bits else "当前上下文未命中历史/反馈/memory"
    gap_text = "；证据缺口：" + "、".join(gaps) if gaps else ""
    return f"当前结论：上游提示未映射场景 {', '.join(vendor_scenarios[:3])}，置信度 {confidence:.2f}；{history}{gap_text}。建议按基础 domain finding 继续复核，并把该场景作为 taxonomy/memory 候选。"


def _default_review_queue_for_domain(domain: SocDomainName) -> str:
    if domain in {SocDomainName.EDR, SocDomainName.HIDS}:
        return "endpoint_review"
    if domain == SocDomainName.APT:
        return "network_review"
    if domain == SocDomainName.WAF_F5:
        return "web_review"
    return "soc_review"


def _used_evidence_sources(request: SocDomainTriageRequest) -> list[str]:
    sources = ["raw_log"]
    if _similar_alert_count(request):
        sources.append("similar_alerts")
    if request.metadata.get("external_disposition_count", 0):
        sources.append("external_feedback")
    if request.metadata.get("relevant_memory_count", 0):
        sources.append("confirmed_memory")
    if successful_evidence(request.investigation_evidence):
        sources.append("read_only_action_evidence")
    elif any(evidence_is_mocked(item) for item in successful_evidence(request.investigation_evidence, include_mocked=True)):
        sources.append("mock_action_evidence")
    return sources


def _scenario_evidence_refs(request: SocDomainTriageRequest) -> list[str]:
    refs = [f"run:{request.run.run_id}", f"alert:{request.run.alert_id}"]
    refs.extend(item.evidence_id for item in successful_evidence(request.investigation_evidence, include_mocked=True))
    if request.correlation_result is not None:
        refs.extend(f"correlation_run:{match.summary.run_id}" for match in request.correlation_result.matches)
        refs.extend(evidence.evidence_id for match in request.correlation_result.matches for evidence in match.reusable_evidence)
    return _dedupe_strs(refs)


def _available_action_routes(request: SocDomainTriageRequest) -> list[str]:
    return successful_evidence_routes(request.investigation_evidence)


def _similar_alert_count(request: SocDomainTriageRequest) -> int:
    if request.correlation_result is not None:
        return len(request.correlation_result.matches)
    value = request.metadata.get("similar_alert_count")
    return value if isinstance(value, int) and value >= 0 else 0


def _correlation_match_count(request: SocDomainTriageRequest) -> int:
    if request.correlation_result is not None:
        return len(request.correlation_result.matches)
    value = request.metadata.get("correlation_match_count")
    return value if isinstance(value, int) and value >= 0 else 0


def _count_status(value: Any) -> str:
    if isinstance(value, int):
        return "available" if value > 0 else "queried_no_match"
    return "not_in_request"


def _matched_terms(corpus: str, keywords: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for keyword in keywords:
        normalized = keyword.lower()
        if normalized in corpus and keyword not in matches:
            matches.append(keyword)
    return matches


def _scenario_corpus(run: AnalysisRun, alert: AlertInput | None) -> str:
    values: list[str] = []
    ignores_processed_fields = _ignores_processed_fields(alert)
    if alert is not None:
        values.extend(
            [
                alert.detection.rule_code or "",
                alert.detection.rule_name or "",
                alert.detection.rule_category or "",
                alert.classification.category or "",
                " ".join(alert.classification.labels),
                " ".join(_vendor_scenarios(alert)),
                _selected_raw_message(alert),
            ]
        )
        values.extend(_flatten_strings(alert.entities.model_dump(mode="json"), limit=80))
        if not ignores_processed_fields:
            values.extend(_flatten_strings(alert.extensions, limit=80))
    if run.analysis is not None:
        values.extend([run.analysis.summary, run.analysis.reason, run.analysis.recommended_action])
    if run.entities is not None:
        values.extend(run.entities.processes)
        values.extend(run.entities.urls)
        values.extend(run.entities.rule_names)
    if run.input_payload is not None and not ignores_processed_fields:
        values.extend(_flatten_strings(run.input_payload, limit=120))
    return " ".join(str(item) for item in values if item).lower()


def _normalized_alert(run: AnalysisRun) -> AlertInput | None:
    if run.input_payload is None:
        return None
    try:
        return normalize_alert_payload(run.input_payload)
    except Exception:  # noqa: BLE001 - domain context should survive historical malformed payloads
        return None


def _vendor_scenarios(alert: AlertInput | None) -> list[str]:
    if alert is None:
        return []
    values: list[str] = [
        alert.classification.category or "",
        alert.detection.rule_category or "",
    ]
    legacy = alert.extensions.get("legacy_platform")
    if isinstance(legacy, Mapping):
        taxonomy = legacy.get("taxonomy")
        if isinstance(taxonomy, Mapping):
            values.extend(str(taxonomy.get(key) or "") for key in ("primary_type", "secondary_type", "tertiary_type", "topic_name"))
    return _dedupe_strs(values)


def _selected_raw_message(alert: AlertInput) -> str:
    policy = alert.extensions.get("evidence_input_policy")
    if not isinstance(policy, Mapping):
        return ""
    paths = [policy.get("selected_input_path")]
    supplementary = policy.get("supplementary_input_paths")
    if isinstance(supplementary, list):
        paths.extend(supplementary)
    values = [_resolve_raw_path(alert.raw, path) for path in paths if isinstance(path, str)]
    return " ".join(value for value in values if isinstance(value, str))


def _ignores_processed_fields(alert: AlertInput | None) -> bool:
    if alert is None:
        return False
    policy = alert.extensions.get("evidence_input_policy")
    return bool(isinstance(policy, Mapping) and policy.get("ignore_processed_fields_for_reasoning") is True)


def _resolve_raw_path(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for part in re.split(r"\.|\[|\]", path):
        if not part:
            continue
        if isinstance(value, Mapping):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            value = value[int(part)] if int(part) < len(value) else None
        else:
            return None
    return value


def _flatten_strings(value: Any, *, limit: int) -> list[str]:
    strings: list[str] = []

    def visit(item: Any) -> None:
        if len(strings) >= limit:
            return
        if isinstance(item, str):
            if item.strip():
                strings.append(item)
            return
        if isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return strings


def _skill_names(skill_context: SocSkillContext) -> list[str]:
    return [item.skill_name for item in skill_context.selected_skills]


def _dedupe_strs(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


__all__ = [
    "SCENARIO_TAXONOMY_VERSION",
    "evidence_profile_for_request",
    "finding_conclusion",
    "scenario_findings",
    "scenario_taxonomy_keys",
    "scenario_taxonomy_snapshot",
]
