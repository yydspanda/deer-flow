"""SOC domain skill resolution backed by DeerFlow's existing skill system."""

from __future__ import annotations

import re
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path

from soc_agent.contracts import (
    AlertEntitySet,
    AlertInput,
    AlertSourceType,
    AlertSummary,
    ExtractedEntities,
    LLMAnalysisRequest,
    RoleResolutionStatus,
    SocSkillContext,
    SocSkillContextItem,
    SocSkillRecommendation,
    SocSkillResolution,
)

SOC_ALERT_TRIAGE_SKILL = "soc-alert-triage"
SOC_ENDPOINT_TRIAGE_SKILL = "soc-endpoint-triage"
SOC_NETWORK_APT_TRIAGE_SKILL = "soc-network-apt-triage"
SOC_WEB_APPLICATION_TRIAGE_SKILL = "soc-web-application-triage"
SOC_EMAIL_PHISHING_TRIAGE_SKILL = "soc-email-phishing-triage"
SOC_ASSET_DIRECTION_SKILL = "soc-asset-direction"
SOC_ASSET_EXTRACTION_SKILL = "soc-asset-extraction"

SOC_LEAD_AGENT_NAME = "soc-triage"
SOC_LEAD_AGENT_SKILLS: tuple[str, ...] = (
    SOC_ALERT_TRIAGE_SKILL,
    SOC_ENDPOINT_TRIAGE_SKILL,
    SOC_NETWORK_APT_TRIAGE_SKILL,
    SOC_WEB_APPLICATION_TRIAGE_SKILL,
    SOC_EMAIL_PHISHING_TRIAGE_SKILL,
    SOC_ASSET_DIRECTION_SKILL,
    SOC_ASSET_EXTRACTION_SKILL,
)
SOC_SKILL_CONTEXT_TOKEN_BUDGET = 240
SOC_SKILL_CONTEXT_SOURCE = "soc_skill_package_projection"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PUBLIC_SKILL_ROOT = _REPO_ROOT / "skills" / "public"
_RUNTIME_GUIDANCE_PATH = Path("references/runtime-guidance.md")
_TOKEN_RE = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")

_SOURCE_SKILLS: dict[AlertSourceType, tuple[str, str]] = {
    AlertSourceType.EDR: (SOC_ENDPOINT_TRIAGE_SKILL, "source_type is edr"),
    AlertSourceType.XDR: (SOC_ENDPOINT_TRIAGE_SKILL, "source_type is xdr"),
    AlertSourceType.HIDS: (SOC_ENDPOINT_TRIAGE_SKILL, "source_type is hids"),
    AlertSourceType.NIDS: (SOC_NETWORK_APT_TRIAGE_SKILL, "source_type is nids"),
    AlertSourceType.NDR: (SOC_NETWORK_APT_TRIAGE_SKILL, "source_type is ndr"),
    AlertSourceType.THREAT_INTEL: (SOC_NETWORK_APT_TRIAGE_SKILL, "source_type is threat_intel"),
    AlertSourceType.WAF: (SOC_WEB_APPLICATION_TRIAGE_SKILL, "source_type is waf"),
    AlertSourceType.F5: (SOC_WEB_APPLICATION_TRIAGE_SKILL, "source_type is f5"),
    AlertSourceType.IAM: (SOC_ASSET_DIRECTION_SKILL, "source_type is iam"),
    AlertSourceType.CLOUD: (SOC_ASSET_DIRECTION_SKILL, "source_type is cloud"),
}

_ENDPOINT_SOURCE_TYPES = frozenset(
    {
        AlertSourceType.EDR,
        AlertSourceType.XDR,
        AlertSourceType.HIDS,
    }
)
_NETWORK_SOURCE_TYPES = frozenset(
    {
        AlertSourceType.NIDS,
        AlertSourceType.NDR,
        AlertSourceType.THREAT_INTEL,
    }
)
_WEB_SOURCE_TYPES = frozenset(
    {
        AlertSourceType.WAF,
        AlertSourceType.F5,
    }
)

_ENDPOINT_KEYWORDS = (
    "edr",
    "xdr",
    "hids",
    "endpoint",
    "process",
    "terminal",
    "lateral",
    "横向",
    "终端",
    "进程",
)
_NETWORK_APT_KEYWORDS = (
    "apt",
    "nids",
    "ndr",
    "c2",
    "beacon",
    "command and control",
    "malicious outbound",
    "callback",
    "ioc",
    "外联",
    "反连",
    "天眼",
)
_WEB_APPLICATION_KEYWORDS = (
    "waf",
    "f5",
    "http",
    "x-forwarded-for",
    "x_forwarded_for",
    "sql injection",
    "xss",
    "webshell",
    "path traversal",
    "web attack",
    "web command",
    "web应用",
    "网页攻击",
    "注入",
)
_WEB_APPLICATION_CONTEXTUAL_KEYWORDS = (
    "command execution",
    "remote code execution",
    "weak password",
    "弱口令",
    "命令执行",
    "文件读取",
    "文件上传",
)
_EMAIL_PHISHING_KEYWORDS = (
    "phishing",
    "suspicious email",
    "email attack",
    "sender spoof",
    "business email compromise",
    "mail attachment",
    "钓鱼邮件",
    "可疑邮件",
    "邮件攻击",
    "发件人",
    "邮件附件",
)
_ASSET_DIRECTION_KEYWORDS = (
    "asset ownership",
    "ownership",
    "victim",
    "attacker",
    "direction",
    "attack direction",
    "攻击方向",
    "资产归属",
    "受害",
    "攻击者",
)
_ASSET_EXTRACTION_KEYWORDS = (
    "asset extraction",
    "extract assets",
    "identify assets",
    "entity extraction",
    "资产提取",
    "资产抽取",
    "提取资产",
    "抽取资产",
)


class SocSkillResolver:
    """Resolve SOC domain skills without reimplementing DeerFlow skill loading."""

    def __init__(
        self,
        *,
        available_skill_names: Iterable[str] = SOC_LEAD_AGENT_SKILLS,
        agent_name: str = SOC_LEAD_AGENT_NAME,
    ) -> None:
        self._available_skill_names = tuple(dict.fromkeys(available_skill_names))
        self._available_skill_set = set(self._available_skill_names)
        self._agent_name = agent_name

    def resolve_for_analysis_request(self, request: LLMAnalysisRequest) -> SocSkillResolution:
        recommendations = _RecommendationBuilder(self._available_skill_set)
        recommendations.add(
            SOC_ALERT_TRIAGE_SKILL,
            reason="baseline SOC triage skill is always applied",
            confidence=0.70,
            matched_field="default",
        )
        _add_source_skill(recommendations, request.source.source_type)
        _add_canonical_entity_skills(recommendations, request.canonical_entities)
        _add_text_skills(
            recommendations,
            [
                request.source.source_system,
                request.source.vendor,
                request.source.product,
                request.detection.rule_code,
                request.detection.rule_name,
                request.detection.rule_category,
                request.detection.detection_key,
                request.classification.category,
                *request.classification.tactic,
                *request.classification.technique,
                *request.classification.labels.values(),
            ],
            source_type=request.source.source_type,
        )
        _add_entity_skills(recommendations, request.extracted_entities)
        _add_role_resolution_skills(recommendations, request)
        if request.evidence_coverage.high_value_gaps:
            recommendations.add(
                SOC_ASSET_EXTRACTION_SKILL,
                reason="evidence coverage reported high-value mapping gaps that require explicit extraction review",
                confidence=0.69,
                matched_field="evidence_coverage.high_value_gaps",
            )
        if request.conflict_count > 0:
            recommendations.add(
                SOC_ASSET_DIRECTION_SKILL,
                reason="fact reconstruction reported field conflicts, so asset/direction review is needed",
                confidence=0.68,
                matched_field="fact_reconstruction.conflict_reports",
            )
        return self._resolution(request.alert_id, recommendations)

    def resolve_for_alert(self, alert: AlertInput, *, entities: ExtractedEntities | None = None) -> SocSkillResolution:
        recommendations = _RecommendationBuilder(self._available_skill_set)
        recommendations.add(
            SOC_ALERT_TRIAGE_SKILL,
            reason="baseline SOC triage skill is always applied",
            confidence=0.70,
            matched_field="default",
        )
        _add_source_skill(recommendations, alert.source.source_type)
        _add_canonical_entity_skills(recommendations, alert.entities)
        _add_text_skills(
            recommendations,
            [
                alert.source.source_system,
                alert.source.vendor,
                alert.source.product,
                alert.detection.rule_code,
                alert.detection.rule_name,
                alert.detection.rule_category,
                alert.detection.detection_key,
                alert.classification.category,
                *alert.classification.tactic,
                *alert.classification.technique,
                *alert.classification.labels.values(),
            ],
            source_type=alert.source.source_type,
        )
        if entities is not None:
            _add_entity_skills(recommendations, entities)
        return self._resolution(alert.alert_id, recommendations)

    def resolve_for_summary(self, summary: AlertSummary) -> SocSkillResolution:
        recommendations = _RecommendationBuilder(self._available_skill_set)
        recommendations.add(
            SOC_ALERT_TRIAGE_SKILL,
            reason="baseline SOC triage skill is always applied",
            confidence=0.70,
            matched_field="default",
        )
        _add_source_skill(recommendations, summary.source_type)
        _add_text_skills(
            recommendations,
            [
                summary.source_system,
                summary.detection_key,
                summary.rule_code,
                summary.rule_name,
                summary.category,
                summary.severity,
            ],
            source_type=summary.source_type,
        )
        _add_summary_entity_skills(
            recommendations,
            summary.entity_keys,
            source_type=summary.source_type,
        )
        return self._resolution(summary.alert_id, recommendations)

    def _resolution(self, alert_id: str | None, recommendations: _RecommendationBuilder) -> SocSkillResolution:
        selected = recommendations.items()
        notes: list[str] = []
        if not selected:
            notes.append("no configured SOC domain skills are available")
        return SocSkillResolution(
            alert_id=alert_id,
            agent_name=self._agent_name,
            selected_skills=selected,
            available_agent_skills=list(self._available_skill_names),
            notes=notes,
        )


def build_soc_skill_context(
    resolution: SocSkillResolution,
    *,
    public_skill_root: Path | None = None,
    token_budget_per_skill: int = SOC_SKILL_CONTEXT_TOKEN_BUDGET,
) -> SocSkillContext:
    """Project reviewed, bounded guidance from selected DeerFlow skill packages."""

    if token_budget_per_skill < 1:
        raise ValueError("token_budget_per_skill must be positive")
    skill_root = public_skill_root or _DEFAULT_PUBLIC_SKILL_ROOT
    items: list[SocSkillContextItem] = []
    notes = list(resolution.notes)
    for recommendation in resolution.selected_skills:
        projected = _project_skill_package(
            skill_root,
            recommendation.skill_name,
            token_budget=token_budget_per_skill,
        )
        if projected is None:
            notes.append(f"valid skill package not found for {recommendation.skill_name}")
            continue
        guidance, guidance_source, guidance_hash, package_hash, estimated_token_count, projection_note = projected
        if projection_note is not None:
            notes.append(projection_note)
        items.append(
            SocSkillContextItem(
                skill_name=recommendation.skill_name,
                reason=recommendation.reason,
                confidence=recommendation.confidence,
                matched_fields=list(recommendation.matched_fields),
                guidance=guidance,
                guidance_source=guidance_source,
                guidance_hash=guidance_hash,
                package_hash=package_hash,
                estimated_token_count=estimated_token_count,
                token_budget=token_budget_per_skill,
            )
        )
    return SocSkillContext(
        source=SOC_SKILL_CONTEXT_SOURCE,
        selected_skills=items,
        total_token_budget=sum(item.token_budget for item in items),
        total_estimated_token_count=sum(item.estimated_token_count for item in items),
        notes=notes,
    )


class _RecommendationBuilder:
    def __init__(self, available_skill_names: set[str]) -> None:
        self._available_skill_names = available_skill_names
        self._items: dict[str, SocSkillRecommendation] = {}

    def add(self, skill_name: str, *, reason: str, confidence: float, matched_field: str) -> None:
        if skill_name not in self._available_skill_names:
            return
        existing = self._items.get(skill_name)
        if existing is None:
            self._items[skill_name] = SocSkillRecommendation(
                skill_name=skill_name,
                reason=reason,
                confidence=confidence,
                matched_fields=[matched_field],
            )
            return
        if confidence > existing.confidence:
            existing.confidence = confidence
            existing.reason = reason
        if matched_field not in existing.matched_fields:
            existing.matched_fields.append(matched_field)

    def items(self) -> list[SocSkillRecommendation]:
        return sorted(self._items.values(), key=lambda item: (-item.confidence, item.skill_name))

    def contains(self, skill_name: str) -> bool:
        return skill_name in self._items


def _add_source_skill(recommendations: _RecommendationBuilder, source_type: AlertSourceType | None) -> None:
    if source_type is None:
        return
    skill = _SOURCE_SKILLS.get(source_type)
    if skill is None:
        return
    skill_name, reason = skill
    recommendations.add(skill_name, reason=reason, confidence=0.78, matched_field="source.source_type")


def _add_entity_skills(recommendations: _RecommendationBuilder, entities: ExtractedEntities) -> None:
    if entities.processes or entities.hosts or entities.users:
        recommendations.add(
            SOC_ENDPOINT_TRIAGE_SKILL,
            reason="endpoint entities include process, host, or user signals",
            confidence=0.72,
            matched_field="extracted_entities.endpoint",
        )
    if entities.emails:
        recommendations.add(
            SOC_EMAIL_PHISHING_TRIAGE_SKILL,
            reason="extracted entities include email addresses",
            confidence=0.74,
            matched_field="extracted_entities.emails",
        )


def _add_canonical_entity_skills(
    recommendations: _RecommendationBuilder,
    entities: AlertEntitySet,
) -> None:
    http = entities.http
    if http.observations or any(
        value is not None
        for value in (
            http.method,
            http.host,
            http.path,
            http.url,
            http.protocol,
            http.port,
            http.status_code,
            http.user_agent,
            http.referer,
            http.x_forwarded_for,
        )
    ):
        recommendations.add(
            SOC_WEB_APPLICATION_TRIAGE_SKILL,
            reason="canonical entities contain HTTP transaction evidence",
            confidence=0.76,
            matched_field="canonical_entities.http",
        )

    email = entities.email
    if email is not None and (email.observations or email.message_id or email.sender_addresses or email.recipient_addresses or email.cc_addresses or email.subject or email.links or email.attachment_names):
        recommendations.add(
            SOC_EMAIL_PHISHING_TRIAGE_SKILL,
            reason="canonical entities contain typed email evidence",
            confidence=0.80,
            matched_field="canonical_entities.email",
        )

    process = entities.process
    user = entities.user
    host = entities.host
    if (
        process.observations
        or any(
            value is not None
            for value in (
                process.process_name,
                process.process_id,
                process.process_path,
                process.command_line,
                process.parent_process_name,
                process.parent_process_id,
                process.parent_command_line,
                process.md5,
                process.sha256,
                user.username,
                user.user_id,
                user.um_account,
                user.src_user,
                user.dst_user,
                host.host_name,
                host.host_id,
            )
        )
        or bool(host.ip_addresses)
    ):
        recommendations.add(
            SOC_ENDPOINT_TRIAGE_SKILL,
            reason="canonical entities contain endpoint, process, user, or host evidence",
            confidence=0.73,
            matched_field="canonical_entities.endpoint",
        )

    network = entities.network
    if (
        network.observations
        or any(
            value is not None
            for value in (
                network.source_ip,
                network.destination_ip,
                network.src_port,
                network.dst_port,
                network.protocol,
                network.application_protocol,
                network.direction,
                network.domain,
                network.url,
            )
        )
        or entities.threat.iocs
        or entities.threat.campaign
        or entities.threat.threat_actor
        or entities.threat.malware_family
    ):
        recommendations.add(
            SOC_NETWORK_APT_TRIAGE_SKILL,
            reason="canonical entities contain wire-session or threat-indicator evidence",
            confidence=0.72,
            matched_field="canonical_entities.network_or_threat",
        )


def _add_role_resolution_skills(
    recommendations: _RecommendationBuilder,
    request: LLMAnalysisRequest,
) -> None:
    for resolution in request.fact_reconstruction.role_resolutions:
        if resolution.role not in {"attacker", "victim", "impacted_asset"}:
            continue
        if resolution.status is not RoleResolutionStatus.CONFLICTED:
            continue
        recommendations.add(
            SOC_ASSET_DIRECTION_SKILL,
            reason="security-role resolution contains competing claims",
            confidence=0.72,
            matched_field=f"fact_reconstruction.role_resolutions:{resolution.role}:{resolution.status.value}",
        )


def _add_summary_entity_skills(
    recommendations: _RecommendationBuilder,
    entity_keys: Iterable[str],
    *,
    source_type: AlertSourceType,
) -> None:
    kinds = {value.partition(":")[0].lower() for value in entity_keys if ":" in value}
    if kinds & {"process", "host", "user", "file_hash"}:
        recommendations.add(
            SOC_ENDPOINT_TRIAGE_SKILL,
            reason="summary contains endpoint entity keys",
            confidence=0.70,
            matched_field="summary.entity_keys:endpoint",
        )
    network_kinds = kinds & {"ip", "domain", "url"}
    if network_kinds and not (network_kinds == {"ip"} and source_type in {AlertSourceType.EDR, AlertSourceType.XDR, AlertSourceType.HIDS}):
        recommendations.add(
            SOC_NETWORK_APT_TRIAGE_SKILL,
            reason="summary contains network entity keys",
            confidence=0.66,
            matched_field="summary.entity_keys:network",
        )
    if "email" in kinds:
        recommendations.add(
            SOC_EMAIL_PHISHING_TRIAGE_SKILL,
            reason="summary contains email entity keys",
            confidence=0.74,
            matched_field="summary.entity_keys:email",
        )


def _add_text_skills(
    recommendations: _RecommendationBuilder,
    values: Iterable[str | None],
    *,
    source_type: AlertSourceType,
) -> None:
    text = " ".join(value for value in values if value).lower()
    if not text:
        return
    _add_keyword_skill(
        recommendations,
        text,
        _ENDPOINT_KEYWORDS,
        SOC_ENDPOINT_TRIAGE_SKILL,
        "endpoint keyword matched in source, detection, classification, or entities",
        allow_create=_keyword_route_may_create(SOC_ENDPOINT_TRIAGE_SKILL, source_type),
    )
    _add_keyword_skill(
        recommendations,
        text,
        _NETWORK_APT_KEYWORDS,
        SOC_NETWORK_APT_TRIAGE_SKILL,
        "network/APT keyword matched in source, detection, classification, or entities",
        allow_create=_keyword_route_may_create(SOC_NETWORK_APT_TRIAGE_SKILL, source_type),
    )
    _add_keyword_skill(
        recommendations,
        text,
        _WEB_APPLICATION_KEYWORDS,
        SOC_WEB_APPLICATION_TRIAGE_SKILL,
        "web-application keyword matched in source, detection, or classification",
        allow_create=_keyword_route_may_create(SOC_WEB_APPLICATION_TRIAGE_SKILL, source_type),
    )
    _add_keyword_skill(
        recommendations,
        text,
        _WEB_APPLICATION_CONTEXTUAL_KEYWORDS,
        SOC_WEB_APPLICATION_TRIAGE_SKILL,
        "web-application behavior keyword matched within typed or source-scoped web context",
        allow_create=source_type in (_NETWORK_SOURCE_TYPES | _WEB_SOURCE_TYPES),
    )
    _add_keyword_skill(
        recommendations,
        text,
        _EMAIL_PHISHING_KEYWORDS,
        SOC_EMAIL_PHISHING_TRIAGE_SKILL,
        "email/phishing keyword matched in source, detection, or classification",
    )
    _add_keyword_skill(
        recommendations,
        text,
        _ASSET_DIRECTION_KEYWORDS,
        SOC_ASSET_DIRECTION_SKILL,
        "asset ownership or attack direction keyword matched",
    )
    _add_keyword_skill(
        recommendations,
        text,
        _ASSET_EXTRACTION_KEYWORDS,
        SOC_ASSET_EXTRACTION_SKILL,
        "asset extraction keyword matched",
    )


def _keyword_route_may_create(skill_name: str, source_type: AlertSourceType) -> bool:
    """Prevent ambiguous text from overriding a known source-domain boundary."""

    if source_type in _ENDPOINT_SOURCE_TYPES:
        return skill_name not in {SOC_NETWORK_APT_TRIAGE_SKILL, SOC_WEB_APPLICATION_TRIAGE_SKILL}
    if source_type in _NETWORK_SOURCE_TYPES:
        return skill_name != SOC_ENDPOINT_TRIAGE_SKILL
    if source_type in _WEB_SOURCE_TYPES:
        return skill_name not in {SOC_ENDPOINT_TRIAGE_SKILL, SOC_NETWORK_APT_TRIAGE_SKILL}
    return True


def _add_keyword_skill(
    recommendations: _RecommendationBuilder,
    text: str,
    keywords: tuple[str, ...],
    skill_name: str,
    reason: str,
    allow_create: bool = True,
) -> None:
    matched = next((keyword for keyword in keywords if keyword in text), None)
    if matched is None or (not allow_create and not recommendations.contains(skill_name)):
        return
    recommendations.add(skill_name, reason=reason, confidence=0.64, matched_field=f"keyword:{matched}")


def _project_skill_package(
    public_skill_root: Path,
    skill_name: str,
    *,
    token_budget: int,
) -> tuple[str, str, str, str, int, str | None] | None:
    from deerflow.skills.parser import parse_skill_file
    from deerflow.skills.types import SkillCategory

    skill_dir = public_skill_root / skill_name
    skill_path = skill_dir / "SKILL.md"
    skill = parse_skill_file(
        skill_path,
        category=SkillCategory.PUBLIC,
        relative_path=Path(skill_name),
    )
    if skill is None or skill.name != skill_name:
        return None

    guidance_path = skill_dir / _RUNTIME_GUIDANCE_PATH
    if guidance_path.is_file():
        guidance = guidance_path.read_text(encoding="utf-8").strip()
        guidance_source = _RUNTIME_GUIDANCE_PATH.as_posix()
    else:
        guidance = skill.description.strip()
        guidance_source = "SKILL.md#description"
    if not guidance:
        return None

    guidance, truncated = _bound_guidance(guidance, token_budget=token_budget)
    estimated_token_count = _estimate_token_count(guidance)
    package_hash = _skill_package_hash(skill_dir)
    if package_hash is None:
        return None
    projection_note = None
    if truncated:
        projection_note = f"runtime guidance truncated to {token_budget} estimated tokens for {skill_name}"
    return (
        guidance,
        guidance_source,
        sha256(guidance.encode("utf-8")).hexdigest(),
        package_hash,
        estimated_token_count,
        projection_note,
    )


def _bound_guidance(value: str, *, token_budget: int) -> tuple[str, bool]:
    matches = list(_TOKEN_RE.finditer(value))
    if len(matches) <= token_budget:
        return value, False
    return value[: matches[token_budget - 1].end()].rstrip(), True


def _estimate_token_count(value: str) -> int:
    return max(1, len(_TOKEN_RE.findall(value)))


def _skill_package_hash(skill_dir: Path) -> str | None:
    files = sorted(path for path in skill_dir.rglob("*") if path.is_file())
    if not files:
        return None
    digest = sha256()
    for path in files:
        relative = path.relative_to(skill_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
