"""Versioned presentation guide for the PingAn corpus DEV workbench.

The guide contains navigation metadata only. It never fabricates Runtime results,
Memory state, analyst labels, or action authority. Every target is checked against
the currently loaded corpus before it is exposed to the browser.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SOC_LEADERSHIP_DEMO_GUIDE_VERSION = "pingan-memory-rehearsal.v2"

SocLeadershipDemoTier = Literal["primary", "backup"]
SocLeadershipDemoAvailability = Literal["ready", "drifted", "unavailable"]
SocLeadershipDemoExpectedMemoryUse = Literal["context_only", "exact_match"]


class SocLeadershipDemoTarget(BaseModel):
    """One corpus group and its stable rehearsal sequence."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    label: str
    source_type: str
    expected_group_id: str
    actual_group_id: str | None = None
    primary_alert_id: str
    rehearsal_alert_ids: list[str] = Field(min_length=1)
    availability: SocLeadershipDemoAvailability
    missing_alert_ids: list[str] = Field(default_factory=list)
    drifted_alert_ids: list[str] = Field(default_factory=list)


class SocLeadershipDemoChapter(BaseModel):
    """One presenter-facing story backed by real corpus targets."""

    model_config = ConfigDict(extra="forbid")

    chapter_id: str
    sequence: int = Field(ge=1)
    tier: SocLeadershipDemoTier
    expected_memory_use: SocLeadershipDemoExpectedMemoryUse
    title: str
    objective: str
    presenter_note: str
    capabilities: list[str] = Field(min_length=1)
    operator_steps: list[str] = Field(min_length=1)
    success_cues: list[str] = Field(min_length=1)
    targets: list[SocLeadershipDemoTarget] = Field(min_length=1)


class SocLeadershipDemoGuide(BaseModel):
    """Read-only, corpus-validated capability walkthrough manifest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.leadership_demo_guide.v2"] = "soc.leadership_demo_guide.v2"
    guide_version: str = SOC_LEADERSHIP_DEMO_GUIDE_VERSION
    title: str = "历史经验如何参与研判"
    purpose: str = "用同一条检测规则下的不同实际行为，对比历史经验何时只作参考、何时可以复用审核结论。"
    ready: bool
    primary_chapter_count: int = Field(ge=1)
    backup_chapter_count: int = Field(ge=0)
    chapters: list[SocLeadershipDemoChapter] = Field(min_length=1)


@dataclass(frozen=True)
class _TargetSpec:
    target_id: str
    label: str
    source_type: str
    expected_group_id: str
    primary_alert_id: str
    rehearsal_alert_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ChapterSpec:
    chapter_id: str
    sequence: int
    tier: SocLeadershipDemoTier
    expected_memory_use: SocLeadershipDemoExpectedMemoryUse
    title: str
    objective: str
    presenter_note: str
    capabilities: tuple[str, ...]
    operator_steps: tuple[str, ...]
    success_cues: tuple[str, ...]
    targets: tuple[_TargetSpec, ...]


_CHAPTERS: tuple[_ChapterSpec, ...] = (
    _ChapterSpec(
        chapter_id="same-rule-context-only",
        sequence=1,
        tier="primary",
        expected_memory_use="context_only",
        title="同一规则、不同场景：经验只作研判参考",
        objective=("OpenVPN+SIP 告警与已确认的纯 OpenVPN 经验具有相同 rule_code，但行为指纹不同，因此经验可以帮助模型理解背景，不能直接改判。"),
        presenter_note="重点看‘仅作研判参考’，证明系统不会因为 rule_code 相同就套用历史结论。",
        capabilities=("同规则多场景", "Context-only", "防止错误改判"),
        operator_steps=(
            "确认纯 OpenVPN 经验已经审核并开放使用。",
            "定位并运行 2480991，保持在当前列表观察运行状态。",
            "完成后点击‘查看结果’，核对经验为‘仅作研判参考’，且未应用决策指令。",
        ),
        success_cues=(
            "两条告警都属于 RPAADM_000558 / 红队IP监控。",
            "目标行为包含 SIP/5060，和纯 OpenVPN 指纹不完全一致。",
            "Memory 可进入上下文，但不能覆盖 Base Decision。",
        ),
        targets=(
            _TargetSpec(
                target_id="red-team-openvpn-sip-context",
                label="OpenVPN + SIP/5060 · 仅作参考",
                source_type="ndr",
                expected_group_id="CG-FF9B8E58B0DE",
                primary_alert_id="2480991",
                rehearsal_alert_ids=("2480991", "2488405"),
            ),
        ),
    ),
    _ChapterSpec(
        chapter_id="same-rule-exact-match",
        sequence=2,
        tier="primary",
        expected_memory_use="exact_match",
        title="同一规则、同一场景：复用审核结论",
        objective=("纯 OpenVPN UDP/1194 告警同时命中相同 rule_code 和强行为指纹；经验经人工审核并开放后，后续同类告警可以复用结论。"),
        presenter_note="重点看 Base、Memory 和 Effective Decision，说明最终变化来自哪条已审核经验。",
        capabilities=("强行为指纹", "精确匹配", "Decision 留痕"),
        operator_steps=(
            "如尚无经验，运行前五条样本并审核生成的 Candidate。",
            "开启未来复用后运行第六条 2455998。",
            "点击‘查看结果’，核对已复用审核结论及完整 Decision lineage。",
        ),
        success_cues=(
            "后续告警与经验同时命中 detection_key 和 behavior_fingerprint。",
            "本次只采用一种 Memory 用法：精确匹配，不重复记为 context-only。",
            "Base、Memory、Effective Decision 和来源 Memory ID 均可追溯。",
        ),
        targets=(
            _TargetSpec(
                target_id="red-team-openvpn-exact",
                label="OpenVPN UDP/1194 · 精确复用",
                source_type="ndr",
                expected_group_id="CG-3E54866F029C",
                primary_alert_id="2455998",
                rehearsal_alert_ids=(
                    "2445395",
                    "2448168",
                    "2457097",
                    "2457177",
                    "2457581",
                    "2455998",
                ),
            ),
        ),
    ),
)


def build_soc_leadership_demo_guide(
    *,
    alert_groups: Mapping[str, str],
) -> SocLeadershipDemoGuide:
    """Validate presentation targets against the loaded corpus index."""

    chapters: list[SocLeadershipDemoChapter] = []
    for chapter in _CHAPTERS:
        targets: list[SocLeadershipDemoTarget] = []
        for target in chapter.targets:
            missing = [alert_id for alert_id in target.rehearsal_alert_ids if alert_id not in alert_groups]
            drifted = [alert_id for alert_id in target.rehearsal_alert_ids if alert_groups.get(alert_id) not in {None, target.expected_group_id}]
            primary_group = alert_groups.get(target.primary_alert_id)
            if primary_group is None:
                availability: SocLeadershipDemoAvailability = "unavailable"
            elif missing or drifted or primary_group != target.expected_group_id:
                availability = "drifted"
            else:
                availability = "ready"
            targets.append(
                SocLeadershipDemoTarget(
                    target_id=target.target_id,
                    label=target.label,
                    source_type=target.source_type,
                    expected_group_id=target.expected_group_id,
                    actual_group_id=primary_group,
                    primary_alert_id=target.primary_alert_id,
                    rehearsal_alert_ids=list(target.rehearsal_alert_ids),
                    availability=availability,
                    missing_alert_ids=missing,
                    drifted_alert_ids=drifted,
                )
            )
        chapters.append(
            SocLeadershipDemoChapter(
                chapter_id=chapter.chapter_id,
                sequence=chapter.sequence,
                tier=chapter.tier,
                expected_memory_use=chapter.expected_memory_use,
                title=chapter.title,
                objective=chapter.objective,
                presenter_note=chapter.presenter_note,
                capabilities=list(chapter.capabilities),
                operator_steps=list(chapter.operator_steps),
                success_cues=list(chapter.success_cues),
                targets=targets,
            )
        )
    return SocLeadershipDemoGuide(
        ready=all(target.availability == "ready" for chapter in chapters for target in chapter.targets),
        primary_chapter_count=sum(chapter.tier == "primary" for chapter in chapters),
        backup_chapter_count=sum(chapter.tier == "backup" for chapter in chapters),
        chapters=chapters,
    )


__all__ = [
    "SOC_LEADERSHIP_DEMO_GUIDE_VERSION",
    "SocLeadershipDemoChapter",
    "SocLeadershipDemoExpectedMemoryUse",
    "SocLeadershipDemoGuide",
    "SocLeadershipDemoTarget",
    "build_soc_leadership_demo_guide",
]
