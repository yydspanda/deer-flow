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

SOC_LEADERSHIP_DEMO_GUIDE_VERSION = "pingan-corpus-leadership-demo.v1"

SocLeadershipDemoTier = Literal["primary", "backup"]
SocLeadershipDemoAvailability = Literal["ready", "drifted", "unavailable"]


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

    schema_version: Literal["soc.leadership_demo_guide.v1"] = "soc.leadership_demo_guide.v1"
    guide_version: str = SOC_LEADERSHIP_DEMO_GUIDE_VERSION
    title: str = "SOC Agent 核心能力验证"
    purpose: str = "按代表性真实语料验证可审计研判、同类分组、Memory 治理与安全边界。"
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
    title: str
    objective: str
    presenter_note: str
    capabilities: tuple[str, ...]
    operator_steps: tuple[str, ...]
    success_cues: tuple[str, ...]
    targets: tuple[_TargetSpec, ...]


_CHAPTERS: tuple[_ChapterSpec, ...] = (
    _ChapterSpec(
        chapter_id="runtime-audit",
        sequence=1,
        tier="primary",
        title="APT 弱口令：一条告警如何形成可审计结论",
        objective="展示原始输入经过 PingAn Adapter、通用 Runtime、LLM 和确定性校验后的完整证据链。",
        presenter_note="先讲系统如何可靠处理一条告警，再讲批量同类经验；不要从 Memory 页面开场。",
        capabilities=("原始数据保留", "规范化", "实体与事实", "有界 LLM 输入", "Grounding", "Decision 留痕"),
        operator_steps=(
            "定位并运行 1965449。",
            "展开运行轨迹，说明每一步都有状态和耗时。",
            "打开完整审计，重点看原始输入、规范化、模型输入、模型输出、Decision 和 Pattern 写入。",
        ),
        success_cues=(
            "十份阶段产物来自同一个持久化 Run。",
            "原始 payload 完整保留，模型只接收裁剪后的类型化证据。",
            "模型结论、引用校验和最终 Decision 分层可追溯。",
        ),
        targets=(
            _TargetSpec(
                target_id="apt-weak-password",
                label="天眼 APT · HTTP 弱口令行为",
                source_type="ndr",
                expected_group_id="CG-1CE3748F0E64",
                primary_alert_id="1965449",
                rehearsal_alert_ids=("1965449",),
            ),
        ),
    ),
    _ChapterSpec(
        chapter_id="same-rule-different-behavior",
        sequence=2,
        tier="primary",
        title="同一规则不等于同一经验",
        objective="对比同属“红队IP监控”的 OpenVPN 与 PLC 漏洞组，证明系统不会按 rule_code 一刀切。",
        presenter_note="这是解释行为指纹价值的关键页面：规则负责粗定位，规范化行为负责可复用边界。",
        capabilities=("同规则细分", "行为指纹", "跨 IP 泛化", "Memory 防误用"),
        operator_steps=(
            "先定位 OpenVPN 组，展示 UDP/1194 与代理隧道特征。",
            "再定位 PLC 组，展示 UDP/44818、CVE-2017-7924 与漏洞利用特征。",
            "对比两个不同 Group ID，强调二者不会共享同一条决策型 Memory。",
        ),
        success_cues=(
            "两个目标具有相同 rule_code 和规则名。",
            "行为摘要、Group ID 和演示名称均不同。",
            "相同规则下仍可形成不同 Business Lesson。",
        ),
        targets=(
            _TargetSpec(
                target_id="red-team-openvpn",
                label="红队IP监控 A · OpenVPN UDP/1194",
                source_type="ndr",
                expected_group_id="CG-3E54866F029C",
                primary_alert_id="2448168",
                rehearsal_alert_ids=("2448168", "2457097", "2457177", "2457581"),
            ),
            _TargetSpec(
                target_id="red-team-plc-cve",
                label="红队IP监控 B · PLC CVE-2017-7924",
                source_type="ndr",
                expected_group_id="CG-541A6F83A997",
                primary_alert_id="2445525",
                rehearsal_alert_ids=("2445525", "2456140", "2461301", "2473700", "2475852"),
            ),
        ),
    ),
    _ChapterSpec(
        chapter_id="benign-memory-loop",
        sequence=3,
        tier="primary",
        title="EDR 重复误报：从 Pattern 到可复用 Business Lesson",
        objective="展示重复告警聚合、Candidate 审核、AI 辅助生成经验以及后续精确匹配改判。",
        presenter_note="现场只实时运行一条；完整五条聚合和审核最好在彩排中预置，避免模型延迟占满汇报时间。",
        capabilities=("Pattern 聚合", "Candidate 治理", "Business Lesson", "精确 Memory", "Base→Effective 对比"),
        operator_steps=(
            "按顺序运行前五条样本，观察支持数达到质量门并生成 Candidate。",
            "审核为误报，生成并确认 Windows 更新部署 Business Lesson，开启未来精确复用。",
            "运行第六条，查看命中的 M-*、Memory Decision 和 Effective Decision。",
        ),
        success_cues=(
            "重复 replay 不重复增加 Pattern 次数。",
            "未经审核的 Candidate 不会直接改变后续结论。",
            "确认后的 Memory 会记录前后 Decision、来源、版本与适用条件。",
        ),
        targets=(
            _TargetSpec(
                target_id="galaxylab-sam-dumping",
                label="GalaxyLab SAM Dump · Windows 更新进程链",
                source_type="edr",
                expected_group_id="CG-D80334698F0C",
                primary_alert_id="1974113",
                rehearsal_alert_ids=("1974113", "1980607", "1980502", "1980722", "1982981", "1984426"),
            ),
        ),
    ),
    _ChapterSpec(
        chapter_id="risk-memory-loop",
        sequence=4,
        tier="primary",
        title="Sliver C2：高风险经验也能受治理复用",
        objective="证明 Memory 不是只会忽略误报；确认的真实风险经验同样可以支持后续转交与处置决策。",
        presenter_note="强调 Memory 负责复用已审核判断，但外部封禁、隔离仍经过独立授权与执行层。",
        capabilities=("真实风险经验", "证据引用", "决策指令", "动作授权分层"),
        operator_steps=(
            "运行前五条 Sliver 心跳样本并形成候选。",
            "审核为真实风险，确认 C2 心跳 Business Lesson。",
            "运行第六条，展示精确命中与决策来源、变化记录。",
        ),
        success_cues=(
            "同一 Memory 可以承载真实风险而非只处理误报。",
            "Memory 改变研判与自动动作授权是两套独立记录。",
            "高风险副作用不会由模型或 Memory 单独越权触发。",
        ),
        targets=(
            _TargetSpec(
                target_id="sliver-c2-heartbeat",
                label="Sliver 远控木马 · HTTP C2 心跳",
                source_type="nids",
                expected_group_id="CG-5734139D64DA",
                primary_alert_id="1979525",
                rehearsal_alert_ids=("1979525", "1979543", "1979582", "1979692", "1979731", "1979722"),
            ),
        ),
    ),
    _ChapterSpec(
        chapter_id="network-role-adjudication",
        sequence=5,
        tier="primary",
        title="反弹 Shell：连接方向不等于攻击角色",
        objective="展示网络 source/destination、attacker/victim 与响应目标分别裁决，避免反连场景方向翻转。",
        presenter_note="说明系统信任上游规则命中和已声明字段语义，但不会把 source 永久硬编码成 attacker。",
        capabilities=("网络方向", "攻击者/受害者", "反向连接", "响应目标", "角色复核"),
        operator_steps=(
            "定位并运行 2452775。",
            "在模型结果和完整审计中查看 Network Direction、Role Adjudication 与 Response Target。",
            "说明可选 Role Verifier 只复核关键角色主张，不重新跑整条研判。",
        ),
        success_cues=(
            "连接发起方与攻击者角色可以不同。",
            "每个角色结论都有来源证据和置信边界。",
            "角色不确定只阻断依赖精确目标的动作，不拖垮有效风险结论。",
        ),
        targets=(
            _TargetSpec(
                target_id="reverse-shell-direction",
                label="Linux 反弹 Shell · TCP/9092",
                source_type="ndr",
                expected_group_id="CG-A9FFA42B4E59",
                primary_alert_id="2452775",
                rehearsal_alert_ids=("2452775", "2460276"),
            ),
        ),
    ),
    _ChapterSpec(
        chapter_id="weak-evidence-boundary",
        sequence=6,
        tier="backup",
        title="可疑邮件：证据不足时不制造伪经验",
        objective="展示 Runtime 仍会给出当前判断，但弱指纹样本不会因为数量多就自动获得决策型 Memory。",
        presenter_note="用于回答‘系统会不会把每条告警都记住’：不会，重复次数不能替代行为特征和人工治理。",
        capabilities=("弱证据边界", "Memory Admission", "人工核查项", "防知识污染"),
        operator_steps=(
            "定位 1965802 并运行。",
            "查看 Runtime 当前结论和 evidence gaps。",
            "查看 Pattern 区域，说明 fingerprint_missing 不能进入决策型候选。",
        ),
        success_cues=(
            "Runtime 有结论，Memory 准入可以同时拒绝沉淀。",
            "六条同规则告警不会自动变成六条 Memory。",
            "系统明确显示缺口，不用 suspicious 掩盖工程失败。",
        ),
        targets=(
            _TargetSpec(
                target_id="siem-suspicious-email",
                label="SIEM 可疑邮件 · 弱行为信号",
                source_type="siem",
                expected_group_id="CG-61EC01772108",
                primary_alert_id="1965802",
                rehearsal_alert_ids=("1965802",),
            ),
        ),
    ),
    _ChapterSpec(
        chapter_id="cross-source-hids",
        sequence=7,
        tier="backup",
        title="HIDS 进程链：厂商字段止于 Adapter",
        objective="展示 HIDS 进程、主机和命令行为如何进入通用实体/事实契约。",
        presenter_note="用于回答‘换一个日志供应商还能不能工作’：新增 Adapter，不修改通用 Runtime。",
        capabilities=("HIDS Adapter", "进程观察", "事实溯源", "跨厂商扩展"),
        operator_steps=(
            "定位并运行 1965448。",
            "查看原始 message、规范化结果和进程事实。",
            "指出 PingAn 字段名只存在于 Adapter provenance，通用层只消费 typed contract。",
        ),
        success_cues=(
            "java、systemd、chattr 等观察带原始路径溯源。",
            "上游原始 payload 仍完整保留。",
            "接入新厂商只需实现同一 Normalizer/Provider 协议。",
        ),
        targets=(
            _TargetSpec(
                target_id="hids-process-chain",
                label="HIDS Web 命令执行 · java/systemd/chattr",
                source_type="hids",
                expected_group_id="CG-C6D5EC376E28",
                primary_alert_id="1965448",
                rehearsal_alert_ids=("1965448",),
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
    "SocLeadershipDemoGuide",
    "SocLeadershipDemoTarget",
    "build_soc_leadership_demo_guide",
]
