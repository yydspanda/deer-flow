"""SOC domain skill resolution backed by DeerFlow's existing skill system."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path

from soc_agent.contracts import (
    AlertInput,
    AlertSourceType,
    AlertSummary,
    ExtractedEntities,
    LLMAnalysisRequest,
    SocSkillContext,
    SocSkillContextItem,
    SocSkillRecommendation,
    SocSkillResolution,
)

SOC_ALERT_TRIAGE_SKILL = "soc-alert-triage"
SOC_ENDPOINT_TRIAGE_SKILL = "soc-endpoint-triage"
SOC_NETWORK_APT_TRIAGE_SKILL = "soc-network-apt-triage"
SOC_WAF_F5_TRIAGE_SKILL = "soc-waf-f5-triage"
SOC_ASSET_DIRECTION_SKILL = "soc-asset-direction"

SOC_LEAD_AGENT_NAME = "soc-triage"
SOC_LEAD_AGENT_SKILLS: tuple[str, ...] = (
    SOC_ALERT_TRIAGE_SKILL,
    SOC_ENDPOINT_TRIAGE_SKILL,
    SOC_NETWORK_APT_TRIAGE_SKILL,
    SOC_WAF_F5_TRIAGE_SKILL,
    SOC_ASSET_DIRECTION_SKILL,
)
SOC_SKILL_CONTEXT_TOKEN_BUDGET = 240
SOC_SKILL_CONTEXT_SOURCE = "soc_skill_resolver"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PUBLIC_SKILL_ROOT = _REPO_ROOT / "skills" / "public"

_SOC_SKILL_CONTEXT_SUMMARIES: dict[str, str] = {
    SOC_ALERT_TRIAGE_SKILL: "General SOC triage: separate facts, inferred facts, conflicts, uncertainty, verdict, confidence, and safe next steps.",
    SOC_ENDPOINT_TRIAGE_SKILL: "Endpoint triage: focus on host/user/process/file evidence, process ancestry, lateral movement, and approval-gated endpoint response.",
    SOC_NETWORK_APT_TRIAGE_SKILL: "Network/APT triage: focus on direction, IOC quality, C2/callback behavior, role assignment, and historical similar alerts.",
    SOC_WAF_F5_TRIAGE_SKILL: "WAF/F5 triage: focus on HTTP evidence, client IP attribution, x-forwarded-for, target application, web attack type, and suppression target.",
    SOC_ASSET_DIRECTION_SKILL: "Asset and direction triage: resolve ownership, attacker/victim roles, traffic direction, affected asset, and response target conflicts.",
}

_SOURCE_SKILLS: dict[AlertSourceType, tuple[str, str]] = {
    AlertSourceType.EDR: (SOC_ENDPOINT_TRIAGE_SKILL, "source_type is edr"),
    AlertSourceType.XDR: (SOC_ENDPOINT_TRIAGE_SKILL, "source_type is xdr"),
    AlertSourceType.HIDS: (SOC_ENDPOINT_TRIAGE_SKILL, "source_type is hids"),
    AlertSourceType.NIDS: (SOC_NETWORK_APT_TRIAGE_SKILL, "source_type is nids"),
    AlertSourceType.NDR: (SOC_NETWORK_APT_TRIAGE_SKILL, "source_type is ndr"),
    AlertSourceType.THREAT_INTEL: (SOC_NETWORK_APT_TRIAGE_SKILL, "source_type is threat_intel"),
    AlertSourceType.WAF: (SOC_WAF_F5_TRIAGE_SKILL, "source_type is waf"),
    AlertSourceType.F5: (SOC_WAF_F5_TRIAGE_SKILL, "source_type is f5"),
    AlertSourceType.IAM: (SOC_ASSET_DIRECTION_SKILL, "source_type is iam"),
    AlertSourceType.CLOUD: (SOC_ASSET_DIRECTION_SKILL, "source_type is cloud"),
}

_ENDPOINT_KEYWORDS = (
    "edr",
    "xdr",
    "hids",
    "endpoint",
    "host",
    "process",
    "terminal",
    "lateral",
    "横向",
    "终端",
    "进程",
    "主机",
)
_NETWORK_APT_KEYWORDS = (
    "apt",
    "nids",
    "ndr",
    "c2",
    "beacon",
    "command and control",
    "malware",
    "ioc",
    "外联",
    "反连",
    "恶意",
    "天眼",
)
_WAF_F5_KEYWORDS = (
    "waf",
    "f5",
    "http",
    "x-forwarded-for",
    "x_forwarded_for",
    "sql injection",
    "xss",
    "webshell",
    "注入",
    "外到内",
)
_ASSET_DIRECTION_KEYWORDS = (
    "asset",
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
        )
        _add_entity_skills(recommendations, request.extracted_entities)
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
                alert.entities.http.host,
                alert.entities.http.url,
                alert.entities.http.x_forwarded_for,
                *alert.classification.tactic,
                *alert.classification.technique,
                *alert.classification.labels.values(),
            ],
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
                *summary.entity_keys,
            ],
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
    """Build compact skill context for bounded prompts and chat streams."""

    skill_root = public_skill_root or _DEFAULT_PUBLIC_SKILL_ROOT
    items: list[SocSkillContextItem] = []
    notes = list(resolution.notes)
    for recommendation in resolution.selected_skills:
        content_hash = _skill_content_hash(skill_root, recommendation.skill_name)
        if content_hash is None:
            notes.append(f"skill file not found for {recommendation.skill_name}")
        items.append(
            SocSkillContextItem(
                skill_name=recommendation.skill_name,
                reason=recommendation.reason,
                confidence=recommendation.confidence,
                matched_fields=list(recommendation.matched_fields),
                summary=_SOC_SKILL_CONTEXT_SUMMARIES.get(
                    recommendation.skill_name,
                    "SOC domain skill selected by resolver; use it only as bounded guidance.",
                ),
                content_hash=content_hash,
                token_budget=token_budget_per_skill,
            )
        )
    return SocSkillContext(
        source=SOC_SKILL_CONTEXT_SOURCE,
        selected_skills=items,
        total_token_budget=sum(item.token_budget for item in items),
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
    if entities.ips or entities.domains or entities.urls:
        recommendations.add(
            SOC_NETWORK_APT_TRIAGE_SKILL,
            reason="network entities include IP, domain, or URL indicators",
            confidence=0.66,
            matched_field="extracted_entities.network",
        )


def _add_text_skills(recommendations: _RecommendationBuilder, values: Iterable[str | None]) -> None:
    text = " ".join(value for value in values if value).lower()
    if not text:
        return
    _add_keyword_skill(
        recommendations,
        text,
        _ENDPOINT_KEYWORDS,
        SOC_ENDPOINT_TRIAGE_SKILL,
        "endpoint keyword matched in source, detection, classification, or entities",
    )
    _add_keyword_skill(
        recommendations,
        text,
        _NETWORK_APT_KEYWORDS,
        SOC_NETWORK_APT_TRIAGE_SKILL,
        "network/APT keyword matched in source, detection, classification, or entities",
    )
    _add_keyword_skill(
        recommendations,
        text,
        _WAF_F5_KEYWORDS,
        SOC_WAF_F5_TRIAGE_SKILL,
        "WAF/F5 keyword matched in source, detection, classification, or HTTP fields",
    )
    _add_keyword_skill(
        recommendations,
        text,
        _ASSET_DIRECTION_KEYWORDS,
        SOC_ASSET_DIRECTION_SKILL,
        "asset ownership or attack direction keyword matched",
    )


def _add_keyword_skill(
    recommendations: _RecommendationBuilder,
    text: str,
    keywords: tuple[str, ...],
    skill_name: str,
    reason: str,
) -> None:
    matched = next((keyword for keyword in keywords if keyword in text), None)
    if matched is None:
        return
    recommendations.add(skill_name, reason=reason, confidence=0.64, matched_field=f"keyword:{matched}")


def _skill_content_hash(public_skill_root: Path, skill_name: str) -> str | None:
    skill_path = public_skill_root / skill_name / "SKILL.md"
    if not skill_path.exists():
        return None
    return sha256(skill_path.read_bytes()).hexdigest()
