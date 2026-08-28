from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertClassification,
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    BoundedAnalysisEvidence,
    DetectionRuleRef,
    EntityKind,
    EntityMention,
    EntrySurface,
    EvidenceItem,
    ExtractedEntities,
    FactReconstructionResult,
    FileEntityRef,
    FileObservationRef,
    FileObservationRelation,
    HostEntityRef,
    HttpEntityRef,
    LLMAnalysisRequest,
    MemoryPatternAggregationPolicy,
    MemoryPatternDataClass,
    MemoryPatternDimension,
    MemoryPatternSourceType,
    NetworkEntityRef,
    ProcessEntityRef,
    RoleResolution,
    RoleResolutionStatus,
    ScenarioHypothesis,
    ServiceRequestContext,
    SocMemoryApplicabilitySpec,
    SocMemoryBusinessLesson,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryDecisionImpact,
    Verdict,
)
from soc_agent.core import SocMemoryPatternService, SocMemoryService, SocServiceError
from soc_agent.memory import (
    ConfirmedMemoryAnalysisRequestEnricher,
    InMemoryMemoryPatternRepository,
    MemoryPatternIneligibleError,
    memory_query_from_analysis_request,
)
from soc_agent.normalizers.alert import normalize_alert_payload

_START = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)


def _context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="pingan-memory-test",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
            roles=["soc_batch_runner"],
        )
    )


def _reviewed_business_lesson(
    conclusion: str = "运营专家确认该模式在审核范围内可复用于后续同类告警。",
) -> SocMemoryBusinessLesson:
    return SocMemoryBusinessLesson(
        conclusion=conclusion,
        business_rationale=["运营专家已核对当前模式的检测事实和业务背景。"],
        applicability_conditions=["必须命中审核后的全部 canonical required facets。"],
        generalization_boundaries=["只有审核范围未约束的实体值可以变化。"],
        invalidation_conditions=["必需 facet 缺失或当前证据出现实质反证时失效。"],
        handling_guidance=["全部适用条件命中后才复用审核结论，否则重新研判。"],
    )


def _run(
    index: int,
    *,
    detection_key: str | None = "pingan:ndr:reverse-shell",
    rule_name: str | None = "Reverse shell detector",
    techniques: list[str] | None = None,
    source_alert_id: str | None = None,
    source_event_id: str | None = None,
    include_source_alert_id: bool = True,
    verdict: Verdict = Verdict.FALSE_POSITIVE,
    network_protocol: str | None = None,
    scenario_type: str | None = None,
    service_url: str | None = None,
    canonical_entities: AlertEntitySet | None = None,
    source_type: AlertSourceType = AlertSourceType.NIDS,
    source_system: str = "zeus",
    product: str = "ndr",
    category: str = "command_and_control",
    primary_evidence_content: str | None = None,
) -> AnalysisRun:
    evidence_ref = "E-000000000001"
    analysis = AnalysisResult(
        verdict=verdict,
        confidence=0.86,
        summary="Reviewed recurring reverse-connection alert.",
        evidence=[
            EvidenceItem(
                evidence_ref=evidence_ref,
                source="canonical",
                description="Reviewed detector hit",
                value=detection_key or "network_anomaly",
            )
        ],
        reasoning=[
            AnalysisReasoningItem(
                reasoning_id="R-01",
                statement="The reviewed event belongs to the same stable detector class.",
                basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                evidence_refs=[evidence_ref],
                confidence=0.86,
            )
        ],
        reason=("Analysts confirmed this recurring class as expected internal activity." if verdict is Verdict.FALSE_POSITIVE else "Analysts confirmed this recurring class as a real security risk."),
        recommended_action=("ignore" if verdict is Verdict.FALSE_POSITIVE else "contain"),
    )
    alert_id = f"PA-ALERT-{index:03d}"
    parsed_url = urlsplit(service_url) if service_url else None
    service_host = parsed_url.hostname if parsed_url is not None else None
    service_path = parsed_url.path if parsed_url is not None else None
    mentions = []
    if service_host:
        mentions.append(
            EntityMention(
                kind=EntityKind.DOMAIN,
                value=service_host,
                key=f"domain:{service_host}",
                role="http_host",
            )
        )
    if service_url:
        normalized_url = service_url.casefold()
        mentions.append(
            EntityMention(
                kind=EntityKind.URL,
                value=normalized_url,
                key=f"url:{normalized_url}",
                role="http_url",
            )
        )
    input_payload = {
        "event_time": (_START + timedelta(minutes=index)).isoformat(),
    }
    if include_source_alert_id:
        input_payload["alert_id"] = source_alert_id or alert_id
    if source_event_id is not None:
        input_payload["event_id"] = source_event_id
    return AnalysisRun(
        run_id=f"PA-RUN-{index:03d}",
        alert_id=alert_id,
        status=AnalysisRunStatus.SUCCESS,
        input_payload=input_payload,
        input_hash=f"{index:064x}",
        started_at=_START + timedelta(minutes=index),
        llm_analysis_request=LLMAnalysisRequest(
            alert_id=alert_id,
            tenant_id="pingan",
            environment="prd",
            source=AlertSourceRef(
                source_type=source_type,
                source_system=source_system,
                vendor="pingan",
                product=product,
                integration_name="pingan_legacy_alert_platform",
            ),
            detection=DetectionRuleRef(
                detection_key=detection_key,
                rule_name=rule_name,
            ),
            classification=AlertClassification(
                category=category,
                severity="high",
                technique=(["T1059", "T1071"] if techniques is None else techniques),
            ),
            canonical_entities=canonical_entities
            or AlertEntitySet(
                network=NetworkEntityRef(
                    protocol=network_protocol,
                    domain=service_host,
                    url=service_url,
                ),
                http=HttpEntityRef(
                    host=service_host,
                    path=service_path,
                    url=service_url,
                ),
            ),
            extracted_entities=ExtractedEntities(mentions=mentions),
            fact_reconstruction=FactReconstructionResult(
                scenario_hypotheses=(
                    [
                        ScenarioHypothesis(
                            scenario_type=scenario_type,
                            status="confirmed",
                            confidence=0.9,
                            rationale="Deterministic test scenario.",
                        )
                    ]
                    if scenario_type
                    else []
                )
            ),
            primary_evidence=(
                BoundedAnalysisEvidence(
                    source_path="alert.hitLog[0].zeusRawLogs[0].message",
                    layer="raw_message",
                    trust_level="high",
                    content=primary_evidence_content,
                )
                if primary_evidence_content is not None
                else None
            ),
        ),
        analysis=analysis,
    )


def _windows_update_entities(
    *,
    class_id: str,
    host_name: str,
    host_ip: str = "10.1.1.1",
    process_path: str = r"C:\WINDOWS\UUS\amd64\wuaucltcore.exe",
    module_name: str = "UpdateDeploy.dll",
    parent_service: str = "wuauserv",
    target_names: tuple[str, ...] = ("SAM", "SYSTEM"),
) -> AlertEntitySet:
    return AlertEntitySet(
        process=ProcessEntityRef(
            process_name="services.exe",
            process_path=process_path,
            command_line=(f'"{process_path}" /DeploymentHandlerFullPath \\\\?\\C:\\WINDOWS\\UUS\\AMD64\\{module_name} /ClassId {class_id} /RunHandlerComServer'),
            parent_process_name="svchost.exe",
            parent_command_line=(f"C:\\Windows\\System32\\svchost.exe -k netsvcs -p -s {parent_service}"),
        ),
        file=FileEntityRef(
            observations=[
                FileObservationRef(
                    observation_id=f"target-{index}",
                    evidence_path=f"message[{index}]#parsed.str_suspicious_file",
                    relation=FileObservationRelation.ENDPOINT_ACTION_TARGET,
                    file_path=(rf"C:\$WinREAgent\Scratch\Mount\Windows\System32\config\{name}"),
                )
                for index, name in enumerate(target_names)
            ]
        ),
        host=HostEntityRef(host_name=host_name, ip_addresses=[host_ip]),
    )


def _windows_update_run(
    index: int,
    *,
    entities: AlertEntitySet,
) -> AnalysisRun:
    return _run(
        index,
        detection_key="leagsoft-edr:rule_code:rpaadm_002010",
        rule_name="GalaxyLab_T1003-SAM-Dumping",
        techniques=["T1003"],
        canonical_entities=entities,
        source_type=AlertSourceType.EDR,
        source_system="leagsoft-edr",
        product="联软edr",
        category="credential_access",
    )


def _service(repository: InMemoryMemoryPatternRepository) -> SocMemoryPatternService:
    return SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=MemoryPatternAggregationPolicy(
            minimum_support=2,
            minimum_distinct_sources=2,
            minimum_conclusive_support=2,
        ),
        profile_registry=build_soc_memory_profile_registry(),
    )


def _observe(service: SocMemoryPatternService, run: AnalysisRun, ref: str):
    return service.observe_run(
        run,
        source_type=MemoryPatternSourceType.BATCH_ALERT,
        transport_ref=ref,
        environment="prd",
        data_class=MemoryPatternDataClass.OPERATIONAL,
        context=_context(),
    )


def test_pingan_profile_creates_one_typed_same_class_candidate() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)

    _observe(service, _run(1), "batch:1")
    result = _observe(service, _run(2), "batch:2")

    assert result.candidate is not None
    assert result.observation.profile_id == "pingan.soc"
    assert result.observation.signature.dimension is MemoryPatternDimension.COMPOUND
    assert result.candidate.applicability is not None
    assert result.candidate.applicability.profile_id == "pingan.soc"
    assert set(result.candidate.applicability.required_facets) == {
        "behavior_fingerprint",
        "behavior_strength",
        "detection_key",
        "detection_signature",
        "environment",
    }
    assert result.candidate.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION
    assert "经验结论" in result.candidate.content
    assert len(repository.list_memory_candidates()) == 1
    assert result.observation.window_end - result.observation.window_start == timedelta(days=1)


def test_pingan_v5_behavior_fingerprint_generalizes_host_class_id_and_hive_subset() -> None:
    profile = build_soc_memory_profile_registry().resolve_run(
        _run(
            1,
            canonical_entities=_windows_update_entities(
                class_id="11111111-1111-1111-1111-111111111111",
                host_name="ENDPOINT-001",
                host_ip="10.1.1.10",
            ),
        )
    )
    first = profile.project_run_facets(
        _run(
            1,
            canonical_entities=_windows_update_entities(
                class_id="11111111-1111-1111-1111-111111111111",
                host_name="ENDPOINT-001",
                host_ip="10.1.1.10",
            ),
        )
    )
    second = profile.project_run_facets(
        _run(
            2,
            canonical_entities=_windows_update_entities(
                class_id="22222222-2222-2222-2222-222222222222",
                host_name="ENDPOINT-999",
                host_ip="10.9.9.99",
                target_names=("SYSTEM",),
            ),
        )
    )

    assert profile.identity.profile_version == "7"
    assert profile.identity.feature_schema_version == "pingan.soc.memory_features.v5"
    assert first["behavior_fingerprint"] == second["behavior_fingerprint"]
    assert first["behavior_component_core"] == second["behavior_component_core"]
    assert {
        "command_module:updatedeploy.dll",
        "parent_service:wuauserv",
        "process_image:wuaucltcore.exe",
        "process_path:windows/uus/amd64/wuaucltcore.exe",
        "target_class:windows_protected_registry_hive",
    } <= set(first["behavior_component_core"])
    assert {"target_file:sam", "target_file:system"} <= set(first["behavior_component"])
    assert "target_file:sam" not in second["behavior_component"]
    assert "target_file:system" in second["behavior_component"]


@pytest.mark.parametrize(
    "changed_entities",
    [
        _windows_update_entities(
            class_id="33333333-3333-3333-3333-333333333333",
            host_name="ENDPOINT-002",
            process_path=r"C:\Temp\wuaucltcore.exe",
        ),
        _windows_update_entities(
            class_id="33333333-3333-3333-3333-333333333333",
            host_name="ENDPOINT-002",
            module_name="UnknownDeploy.dll",
        ),
        _windows_update_entities(
            class_id="33333333-3333-3333-3333-333333333333",
            host_name="ENDPOINT-002",
            parent_service="RemoteRegistry",
        ),
    ],
)
def test_pingan_v5_behavior_fingerprint_rejects_material_component_changes(
    changed_entities: AlertEntitySet,
) -> None:
    profile = build_soc_memory_profile_registry().resolve_run(_run(1))
    baseline = profile.project_run_facets(
        _run(
            1,
            canonical_entities=_windows_update_entities(
                class_id="11111111-1111-1111-1111-111111111111",
                host_name="ENDPOINT-001",
            ),
        )
    )
    changed = profile.project_run_facets(
        _run(
            2,
            canonical_entities=changed_entities,
        )
    )

    assert baseline["behavior_fingerprint"] != changed["behavior_fingerprint"]


def test_pingan_v5_network_behavior_splits_same_rule_by_service_and_vulnerability() -> None:
    profile = build_soc_memory_profile_registry().resolve_run(_run(1))
    openvpn_runs = [
        _run(
            index,
            detection_key="sec_guard_apt:rule_code:rpaadm_000558",
            rule_name="红队IP监控",
            techniques=["T1190"],
            canonical_entities=AlertEntitySet(
                network=NetworkEntityRef(
                    source_ip=f"199.45.154.{170 + index}",
                    destination_ip=f"124.196.28.{60 + index}",
                    src_port=40_000 + index,
                    dst_port=1194,
                    protocol="udp",
                )
            ),
            category="代理工具",
        )
        for index in range(1, 5)
    ]
    plc_run = _run(
        5,
        detection_key="sec_guard_apt:rule_code:rpaadm_000558",
        rule_name="红队IP监控",
        techniques=["T1190"],
        canonical_entities=AlertEntitySet(
            network=NetworkEntityRef(
                source_ip="36.32.3.212",
                destination_ip="124.196.50.90",
                src_port=26392,
                dst_port=44818,
                protocol="udp",
            )
        ),
        category="拒绝服务",
        primary_evidence_content=json.dumps(
            {
                "rule_name": "Rockwell Automation拒绝服务漏洞(CVE-2017-7924)",
                "description": "Remote PCCC packet may cause a denial of service.",
            },
            ensure_ascii=False,
        ),
    )

    openvpn_facets = [profile.project_run_facets(run) for run in openvpn_runs]
    plc_facets = profile.project_run_facets(plc_run)
    openvpn_signature_items = [profile.build_pattern_signature(run, facets=facets) for run, facets in zip(openvpn_runs, openvpn_facets, strict=True)]
    openvpn_signatures = {item.value for item in openvpn_signature_items}
    plc_signature_item = profile.build_pattern_signature(
        plc_run,
        facets=plc_facets,
    )
    plc_signature = plc_signature_item.value

    assert len(openvpn_signatures) == 1
    assert plc_signature not in openvpn_signatures
    assert {item.label for item in openvpn_signature_items} == {"OpenVPN / UDP 1194"}
    assert plc_signature_item.label == "CVE-2017-7924 / 拒绝服务 / UDP 44818"
    assert all(len(item.facets) <= 20 for item in [*openvpn_signature_items, plc_signature_item])
    assert all(facets["network_service"] == ["udp/1194"] for facets in openvpn_facets)
    assert all(facets["attack_behavior_family"] == ["proxy_tunnel_activity"] for facets in openvpn_facets)
    assert plc_facets["network_service"] == ["udp/44818"]
    assert plc_facets["vulnerability_id"] == ["CVE-2017-7924"]
    assert set(plc_facets["attack_behavior_family"]) == {
        "denial_of_service",
        "vulnerability_exploitation",
    }


def test_pingan_endpoint_pattern_label_explains_the_behavior() -> None:
    run = _windows_update_run(
        1,
        entities=_windows_update_entities(
            class_id="11111111-1111-1111-1111-111111111111",
            host_name="ENDPOINT-001",
        ),
    )
    profile = build_soc_memory_profile_registry().resolve_run(run)
    facets = profile.project_run_facets(run)

    signature = profile.build_pattern_signature(run, facets=facets)

    assert signature.label == ("wuaucltcore.exe / wuauserv 服务 / Windows 受保护注册表配置单元")


def test_pingan_profile_rejects_cross_behavior_memory_retrieval_for_same_rule() -> None:
    profile = build_soc_memory_profile_registry().resolve_run(_run(1))
    http_run = _run(
        6,
        detection_key="sec_guard_apt:rule_code:rpaadm_000558",
        rule_name="红队IP监控",
        techniques=["T1190"],
        canonical_entities=AlertEntitySet(
            network=NetworkEntityRef(
                source_ip="36.32.3.213",
                destination_ip="124.196.50.91",
                src_port=38117,
                dst_port=80,
                protocol="http",
            ),
            http=HttpEntityRef(
                method="GET",
                host="service.example.internal",
                path="/health",
                port=80,
                protocol="http",
            ),
        ),
        category="web_attack",
    )
    plc_run = _run(
        7,
        detection_key="sec_guard_apt:rule_code:rpaadm_000558",
        rule_name="红队IP监控",
        techniques=["T1190"],
        canonical_entities=AlertEntitySet(
            network=NetworkEntityRef(
                source_ip="36.32.3.212",
                destination_ip="124.196.50.90",
                src_port=26392,
                dst_port=44818,
                protocol="udp",
            )
        ),
        category="拒绝服务",
        primary_evidence_content=json.dumps(
            {
                "rule_name": "Rockwell Automation拒绝服务漏洞(CVE-2017-7924)",
            },
            ensure_ascii=False,
        ),
    )

    http_facets = profile.project_run_facets(http_run)
    plc_facets = profile.project_run_facets(plc_run)

    assert (
        profile.retrieval_conflict_reasons(
            record_facets=http_facets,
            query_facets=http_facets,
        )
        == []
    )
    assert set(
        profile.retrieval_conflict_reasons(
            record_facets=plc_facets,
            query_facets=http_facets,
        )
    ) == {
        "network_service_mismatch",
        "vulnerability_scope_missing",
        "attack_behavior_family_mismatch",
    }


def test_pingan_v5_reviewed_endpoint_memory_generalizes_entities_without_generalizing_behavior() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(
        pattern_service,
        _windows_update_run(
            1,
            entities=_windows_update_entities(
                class_id="11111111-1111-1111-1111-111111111111",
                host_name="ENDPOINT-001",
                host_ip="10.1.1.10",
            ),
        ),
        "batch:1",
    )
    aggregated = _observe(
        pattern_service,
        _windows_update_run(
            2,
            entities=_windows_update_entities(
                class_id="22222222-2222-2222-2222-222222222222",
                host_name="ENDPOINT-002",
                host_ip="10.2.2.20",
                target_names=("SYSTEM",),
            ),
        ),
        "batch:2",
    )
    assert aggregated.candidate is not None

    reviewed_at = _START + timedelta(hours=1)
    memory_service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
        profile_registry=build_soc_memory_profile_registry(),
        now_provider=lambda: reviewed_at,
    )
    reviewed = memory_service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=aggregated.candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="运营确认该 Windows Update 行为模式属于可复用的无风险误报。",
            record_lesson=SocMemoryBusinessLesson(
                conclusion="Windows Update 的 wuaucltcore.exe 在审核行为范围内读取受保护注册表配置单元，属于无风险误报。",
                business_rationale=["运营确认 UUS、wuauserv 和 UpdateDeploy.dll 组合是受管 Windows 更新行为。"],
                applicability_conditions=["检测规则、检测器签名和 Profile v5 core behavior fingerprint 必须全部一致。"],
                generalization_boundaries=["主机、IP、账号、ClassId 以及 SAM/SYSTEM 子集可以变化。"],
                invalidation_conditions=["进程路径、命令模块、父服务或目标类型变化时不得沿用该结论。"],
                handling_guidance=["精确适用时复用 false_positive；否则按当前告警重新研判。"],
            ),
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
            clear_review_on_match=True,
            activate_retrieval=True,
            activation_valid_until=reviewed_at + timedelta(days=60),
            activation_review_after_days=30,
        ),
        context=ServiceRequestContext(
            idempotency_key="confirm-pingan-windows-update-v5",
            actor=ActorContext(
                actor_id="memory-reviewer",
                actor_type=ActorType.USER,
                surface=EntrySurface.TEST,
                roles=["soc_memory_reviewer"],
            ),
        ),
    )
    assert reviewed.memory_record is not None

    exact_request = _windows_update_run(
        3,
        entities=_windows_update_entities(
            class_id="33333333-3333-3333-3333-333333333333",
            host_name="ENDPOINT-999",
            host_ip="10.9.9.99",
        ),
    ).llm_analysis_request
    changed_request = _windows_update_run(
        4,
        entities=_windows_update_entities(
            class_id="44444444-4444-4444-4444-444444444444",
            host_name="ENDPOINT-999",
            host_ip="10.9.9.99",
            parent_service="RemoteRegistry",
        ),
    ).llm_analysis_request
    assert exact_request is not None
    assert changed_request is not None

    registry = build_soc_memory_profile_registry()
    exact = memory_service.find_relevant_records(
        memory_query_from_analysis_request(
            exact_request,
            profile=registry.resolve_request(exact_request),
        )
    )
    changed = memory_service.find_relevant_records(
        memory_query_from_analysis_request(
            changed_request,
            profile=registry.resolve_request(changed_request),
        )
    )

    assert [item.memory_id for item in exact.matches] == [reviewed.memory_record.memory_id]
    assert exact.matches[0].applicability_report is not None
    assert exact.matches[0].applicability_report.status.value == "applicable"

    enriched_exact = ConfirmedMemoryAnalysisRequestEnricher(
        memory_service,
        profile_registry=registry,
        environment="prd",
    )(exact_request)
    memory_context = [item for item in enriched_exact.context_catalog if item.kind.value == "confirmed_memory"]
    assert len(memory_context) == 1
    assert memory_context[0].metadata["decision_directive_applicable"] is True
    assert memory_context[0].memory_comparison is not None
    assert memory_context[0].memory_comparison.use_mode.value == "directive_applicable"

    if changed.matches:
        assert changed.matches[0].applicability_report is not None
        assert changed.matches[0].applicability_report.status.value != "applicable"
        assert changed.matches[0].applicability_report.context_only_allowed is True
        enriched_changed = ConfirmedMemoryAnalysisRequestEnricher(
            memory_service,
            profile_registry=registry,
            environment="prd",
        )(changed_request)
        changed_context = [item for item in enriched_changed.context_catalog if item.kind.value == "confirmed_memory"]
        assert changed_context
        assert changed_context[0].metadata["decision_directive_applicable"] is False
        assert changed_context[0].memory_comparison is not None
        assert changed_context[0].memory_comparison.use_mode.value == "context_only"


def test_pingan_profile_rejects_category_only_cohorts() -> None:
    with pytest.raises(
        MemoryPatternIneligibleError,
        match="canonical detection key or behavior fingerprint",
    ):
        _observe(
            _service(InMemoryMemoryPatternRepository()),
            _run(1, detection_key=None, techniques=[]),
            "batch:1",
        )


def test_pingan_profile_uses_deterministic_behavior_when_rule_identity_is_absent() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    first = _run(
        1,
        detection_key=None,
        rule_name=None,
        techniques=["T1059", "T1071"],
    )
    second = _run(
        2,
        detection_key=None,
        rule_name=None,
        techniques=["T1059", "T1071"],
    )

    first_result = _observe(service, first, "batch:1")
    second_result = _observe(service, second, "batch:2")

    assert first_result.observation.signature.dimension is MemoryPatternDimension.BEHAVIOR
    assert second_result.candidate is not None
    assert second_result.candidate.applicability is not None
    assert set(second_result.candidate.applicability.required_facets) == {
        "behavior_fingerprint",
        "behavior_strength",
        "environment",
    }
    assert second_result.candidate.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION


def test_pingan_profile_deduplicates_one_upstream_occurrence() -> None:
    service = _service(InMemoryMemoryPatternRepository())
    first = _observe(
        service,
        _run(
            1,
            source_alert_id="ZEUS-ALERT-001",
            source_event_id="SENSOR-EVENT-001",
        ),
        "batch:1",
    )
    duplicate = _observe(
        service,
        _run(
            2,
            source_alert_id="ZEUS-ALERT-001",
            source_event_id="SENSOR-EVENT-CHANGED",
        ),
        "batch:2",
    )

    assert duplicate.duplicate_occurrence is True
    assert duplicate.observation.observation_id == first.observation.observation_id
    assert duplicate.support_count == 1
    assert duplicate.candidate is None


def test_pingan_profile_counts_new_alert_id_as_new_occurrence() -> None:
    service = _service(InMemoryMemoryPatternRepository())
    first = _observe(
        service,
        _run(
            1,
            source_alert_id="ZEUS-ALERT-001",
            source_event_id="SENSOR-EVENT-SHARED",
        ),
        "batch:1",
    )
    second = _observe(
        service,
        _run(
            2,
            source_alert_id="ZEUS-ALERT-002",
            source_event_id="SENSOR-EVENT-SHARED",
        ),
        "batch:2",
    )

    assert first.support_count == 1
    assert second.duplicate_occurrence is False
    assert second.support_count == 2


def test_pingan_profile_uses_event_id_when_alert_id_is_missing() -> None:
    service = _service(InMemoryMemoryPatternRepository())
    first = _observe(
        service,
        _run(
            1,
            include_source_alert_id=False,
            source_event_id="SENSOR-EVENT-001",
        ),
        "batch:1",
    )
    duplicate = _observe(
        service,
        _run(
            2,
            include_source_alert_id=False,
            source_event_id="SENSOR-EVENT-001",
        ),
        "batch:2",
    )

    assert duplicate.duplicate_occurrence is True
    assert duplicate.observation.observation_id == first.observation.observation_id
    assert duplicate.support_count == 1


def test_pingan_profile_uses_nested_zeus_alert_id_before_payload_hash() -> None:
    service = _service(InMemoryMemoryPatternRepository())
    first_run = _run(1, include_source_alert_id=False)
    first_run.input_payload = {
        "alert": {
            "alertId": "2025642",
            "createAt": "2026-06-16 19:51:14",
            "hitLog": [],
        },
        "delivery_marker": "first",
    }
    first_run.input_hash = "a" * 64
    duplicate_run = _run(2, include_source_alert_id=False)
    duplicate_run.input_payload = {
        "alert": {
            "alertId": "2025642",
            "createAt": "2026-06-16 19:51:14",
            "hitLog": [],
        },
        "delivery_marker": "changed",
    }
    duplicate_run.input_hash = "b" * 64

    normalized = normalize_alert_payload(first_run.input_payload)
    assert normalized.event.event_time is not None
    assert normalized.event.event_time.utcoffset() == timedelta(hours=8)
    assert normalized.extensions["event_time_policy"] == {
        "naive_timezone": "Asia/Shanghai",
        "event_time_timezone_assumed": True,
        "received_at_timezone_assumed": True,
    }

    first = _observe(service, first_run, "kafka:topic:0:1")
    duplicate = _observe(service, duplicate_run, "kafka:topic:0:2")

    assert duplicate.duplicate_occurrence is True
    assert duplicate.observation.observation_id == first.observation.observation_id
    assert duplicate.support_count == 1


def test_pingan_profile_is_server_selected_for_runtime_query() -> None:
    request = _run(1).llm_analysis_request
    assert request is not None
    registry = build_soc_memory_profile_registry()
    query = memory_query_from_analysis_request(
        request,
        profile=registry.resolve_request(request),
    )

    assert query.metadata["memory_profile_id"] == "pingan.soc"
    assert query.metadata["memory_feature_schema_version"] == ("pingan.soc.memory_features.v5")
    assert query.facets["detection_key"] == ["pingan:ndr:reverse-shell"]
    assert len(query.facets["detection_signature"]) == 1
    assert query.facets["behavior_strength"] == ["strong"]


def test_pingan_profile_defaults_to_thirty_day_fixed_window() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        profile_registry=build_soc_memory_profile_registry(),
    )

    result = _observe(service, _run(1), "batch:thirty-day-window")

    assert result.observation.profile_version == "7"
    assert result.observation.window_end - result.observation.window_start == timedelta(days=30)
    assert result.observation.aggregation_policy.window_seconds == 30 * 24 * 60 * 60


def test_reviewer_can_confirm_activate_and_authorize_exact_future_matches() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    assert aggregated.candidate is not None

    reviewed_at = _START + timedelta(hours=1)
    result = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
        now_provider=lambda: reviewed_at,
    ).review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=aggregated.candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Reviewed cohort is reusable for the exact PingAn detector class.",
            record_lesson=_reviewed_business_lesson("Reviewed cohort is reusable for the exact PingAn detector class."),
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
            clear_review_on_match=True,
            activate_retrieval=True,
            activation_valid_until=reviewed_at + timedelta(days=30),
            activation_review_after_days=7,
        ),
        context=ServiceRequestContext(
            idempotency_key="confirm-pingan-memory-001",
            actor=ActorContext(
                actor_id="memory-reviewer",
                actor_type=ActorType.USER,
                surface=EntrySurface.TEST,
                roles=["soc_memory_reviewer"],
            ),
        ),
    )

    assert result.memory_record is not None
    assert result.memory_record.retrieval_enabled is True
    assert result.memory_record.decision_directive is not None
    assert result.memory_record.decision_directive.target_verdict is Verdict.FALSE_POSITIVE
    assert result.memory_record.applicability is not None
    assert result.memory_record.applicability.profile_id == "pingan.soc"
    assert result.memory_record.business_lesson is not None
    assert result.memory_record.business_lesson.conclusion == ("Reviewed cohort is reusable for the exact PingAn detector class.")
    assert "适用条件 / Applicability" in result.memory_record.content
    assert result.memory_record.metadata["business_lesson_source"] == ("reviewer_supplied")


def test_decision_bearing_memory_rejects_missing_explicit_business_lesson() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    assert aggregated.candidate is not None

    with pytest.raises(
        ValueError,
        match="explicit reviewed record_lesson",
    ):
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
        ).review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=aggregated.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="Reviewed simulation lesson for Reverse connection detector.",
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                )
            ),
        )

    assert repository.get_memory_candidate(aggregated.candidate.candidate_id).status.value == "pending_review"


def test_reviewer_persists_askbob_business_lesson_and_exact_service_scope() -> None:
    askbob_url = "https://paic.com.cn/pws/askbob-gpt"
    askbob_entity = f"url:{askbob_url}"
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1, service_url=askbob_url), "batch:1")
    aggregated = _observe(
        pattern_service,
        _run(2, service_url=askbob_url),
        "batch:2",
    )
    candidate = aggregated.candidate
    assert candidate is not None
    base = candidate.applicability
    assert base is not None
    assert askbob_entity in candidate.facets["entity"]

    narrowed = SocMemoryApplicabilitySpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "required_facets": {
                **base.required_facets,
                "entity": [askbob_entity],
            },
            "optional_facets": {key: values for key, values in base.optional_facets.items() if key != "entity"},
            "context_only_required_facet_keys": sorted({*base.context_only_required_facet_keys, "entity"}),
        }
    )
    lesson = SocMemoryBusinessLesson(
        conclusion=("该流量访问平安内部 AskBob LLM 服务，不是真实反弹 Shell。"),
        business_rationale=["运营专家确认 canonical URL https://paic.com.cn/pws/askbob-gpt 属于内部 LLM 调用。"],
        applicability_conditions=["相同检测规则、检测器签名、强行为指纹、环境和精确 AskBob URL。"],
        generalization_boundaries=["源和目的 IP 可以变化；服务身份由精确 canonical URL 约束。"],
        invalidation_conditions=["URL 缺失或变化，或者当前告警出现真实 Shell、命令执行或恶意载荷反证。"],
        handling_guidance=["全部必需条件命中时复用 false_positive，否则按当前告警重新研判。"],
    )
    reviewed_at = _START + timedelta(hours=1)
    record = (
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
            mutation_audit_repository=repository,
            now_provider=lambda: reviewed_at,
        )
        .review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="运营专家确认该精确 AskBob 服务模式可以作为未来误报经验复用。",
                record_lesson=lesson,
                record_applicability=narrowed,
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
                activate_retrieval=True,
                activation_valid_until=reviewed_at + timedelta(days=60),
                activation_review_after_days=30,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                )
            ),
        )
        .memory_record
    )
    assert record is not None
    assert record.business_lesson == lesson
    assert record.summary == lesson.conclusion
    assert record.applicability == narrowed
    assert record.metadata["business_lesson_source"] == "reviewer_supplied"

    registry = build_soc_memory_profile_registry()
    exact_request = _run(3, service_url=askbob_url).llm_analysis_request
    other_request = _run(
        4,
        service_url="https://unreviewed.example/pws/askbob-gpt",
    ).llm_analysis_request
    assert exact_request is not None
    assert other_request is not None
    service = SocMemoryService(
        record_repository=repository,
        now_provider=lambda: reviewed_at,
    )
    exact = service.find_relevant_records(
        memory_query_from_analysis_request(
            exact_request,
            profile=registry.resolve_request(exact_request),
        )
    )
    different_service = service.find_relevant_records(
        memory_query_from_analysis_request(
            other_request,
            profile=registry.resolve_request(other_request),
        )
    )

    assert [item.memory_id for item in exact.matches] == [record.memory_id]
    assert different_service.matches == []


def test_pingan_profile_requires_the_reviewed_environment_for_retrieval() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    assert aggregated.candidate is not None

    reviewed_at = _START + timedelta(hours=1)
    record = (
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
            mutation_audit_repository=repository,
            now_provider=lambda: reviewed_at,
        )
        .review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=aggregated.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="The exact detector class is reusable only in the reviewed environment.",
                record_lesson=_reviewed_business_lesson("The exact detector class is reusable only in the reviewed environment."),
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
                activate_retrieval=True,
                activation_valid_until=reviewed_at + timedelta(days=30),
                activation_review_after_days=7,
            ),
            context=ServiceRequestContext(
                idempotency_key="confirm-pingan-memory-environment",
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                ),
            ),
        )
        .memory_record
    )
    assert record is not None

    registry = build_soc_memory_profile_registry()
    prd_request = _run(3).llm_analysis_request
    assert prd_request is not None
    prd_query = memory_query_from_analysis_request(
        prd_request,
        profile=registry.resolve_request(prd_request),
    )
    stg_request = prd_request.model_copy(update={"environment": "stg"})
    stg_query = memory_query_from_analysis_request(
        stg_request,
        profile=registry.resolve_request(stg_request),
    )
    memory_service = SocMemoryService(
        record_repository=repository,
        now_provider=lambda: reviewed_at,
    )

    assert [item.memory_id for item in memory_service.find_relevant_records(prd_query).matches] == [record.memory_id]
    stg_result = memory_service.find_relevant_records(stg_query)
    assert stg_result.matches == []
    assert stg_result.skipped_not_applicable == 1


def test_pingan_detection_only_candidate_is_rule_context_not_decision_authority() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1, techniques=[]), "batch:1")
    aggregated = _observe(pattern_service, _run(2, techniques=[]), "batch:2")

    assert aggregated.candidate is not None
    assert aggregated.observation.signature.dimension is MemoryPatternDimension.DETECTION
    assert aggregated.candidate.decision_impact is SocMemoryDecisionImpact.REVIEW_HINT
    assert aggregated.candidate.metadata["decision_scope"] == "rule_context_only"

    with pytest.raises(
        SocServiceError,
        match="behavior-scoped decision-eligible candidate",
    ):
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
        ).review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=aggregated.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="Same detector is useful background but not a universal verdict.",
                record_lesson=_reviewed_business_lesson("Same detector is useful background but not a universal verdict."),
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                )
            ),
        )


def test_reviewer_can_narrow_pattern_decision_scope_with_candidate_facets() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    candidate = aggregated.candidate
    assert candidate is not None
    base = candidate.applicability
    assert base is not None

    required = {**base.required_facets, "source_type": ["nids"]}
    optional = {key: values for key, values in base.optional_facets.items() if key != "source_type"}
    narrowed = SocMemoryApplicabilitySpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "required_facets": required,
            "optional_facets": optional,
            "context_only_required_facet_keys": [
                *base.context_only_required_facet_keys,
                "source_type",
            ],
        }
    )
    reviewed = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
    ).review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Only the reviewed NIDS pattern owns this future verdict.",
            record_lesson=_reviewed_business_lesson("Only the reviewed NIDS pattern owns this future verdict."),
            record_applicability=narrowed,
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
        ),
        context=ServiceRequestContext(
            actor=ActorContext(
                actor_id="memory-reviewer",
                actor_type=ActorType.USER,
                surface=EntrySurface.TEST,
                roles=["soc_memory_reviewer"],
            )
        ),
    )

    assert reviewed.memory_record is not None
    assert reviewed.memory_record.applicability == narrowed
    assert reviewed.memory_record.decision_directive is not None
    assert set(reviewed.memory_record.decision_directive.required_facet_keys) == {
        "behavior_fingerprint",
        "behavior_strength",
        "detection_key",
        "detection_signature",
        "environment",
        "source_type",
    }


def test_reviewer_cannot_remove_the_compound_behavior_anchor() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    candidate = aggregated.candidate
    assert candidate is not None
    base = candidate.applicability
    assert base is not None
    widened = SocMemoryApplicabilitySpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "required_facets": {key: values for key, values in base.required_facets.items() if key != "behavior_fingerprint"},
            "context_only_required_facet_keys": [],
            "context_only_missing_facet_keys": [],
            "context_only_similarity_facet_keys": [],
        }
    )

    with pytest.raises(
        SocServiceError,
        match="cannot remove candidate required facets: behavior_fingerprint",
    ):
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
        ).review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="Attempted broad rule-only scope must fail closed.",
                record_applicability=widened,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                )
            ),
        )


def test_same_rule_different_behaviors_and_opposite_outcomes_form_separate_candidates() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)

    _observe(
        service,
        _run(1, techniques=["T1059", "T1071"], verdict=Verdict.FALSE_POSITIVE),
        "batch:1",
    )
    benign = _observe(
        service,
        _run(2, techniques=["T1059", "T1071"], verdict=Verdict.FALSE_POSITIVE),
        "batch:2",
    )
    _observe(
        service,
        _run(3, techniques=["T1059", "T1021"], verdict=Verdict.TRUE_POSITIVE),
        "batch:3",
    )
    risky = _observe(
        service,
        _run(4, techniques=["T1059", "T1021"], verdict=Verdict.TRUE_POSITIVE),
        "batch:4",
    )

    assert benign.candidate is not None
    assert risky.candidate is not None
    assert benign.observation.signature.value != risky.observation.signature.value
    assert benign.candidate.candidate_type.value == "benign_pattern"
    assert risky.candidate.candidate_type.value == "detection_lesson"
    assert len(repository.list_memory_candidates()) == 2


def test_same_rule_similar_behavior_is_context_only_until_exact_fingerprint_matches() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1, techniques=["T1059", "T1071"]), "batch:1")
    aggregated = _observe(
        pattern_service,
        _run(2, techniques=["T1059", "T1071"]),
        "batch:2",
    )
    assert aggregated.candidate is not None

    reviewed_at = _START + timedelta(hours=1)
    record = (
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
            mutation_audit_repository=repository,
            now_provider=lambda: reviewed_at,
        )
        .review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=aggregated.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="Exact detector and behavior pair is a reviewed benign pattern.",
                record_lesson=_reviewed_business_lesson("Exact detector and behavior pair is a reviewed benign pattern."),
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
                activate_retrieval=True,
                activation_valid_until=reviewed_at + timedelta(days=30),
                activation_review_after_days=7,
            ),
            context=ServiceRequestContext(
                idempotency_key="confirm-pingan-memory-context-only",
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                ),
            ),
        )
        .memory_record
    )
    assert record is not None

    registry = build_soc_memory_profile_registry()
    exact_request = _run(3, techniques=["T1059", "T1071"]).llm_analysis_request
    similar_request = _run(4, techniques=["T1059", "T1021"]).llm_analysis_request
    assert exact_request is not None
    assert similar_request is not None
    exact_query = memory_query_from_analysis_request(
        exact_request,
        profile=registry.resolve_request(exact_request),
    )
    similar_query = memory_query_from_analysis_request(
        similar_request,
        profile=registry.resolve_request(similar_request),
    )
    memory_service = SocMemoryService(
        record_repository=repository,
        now_provider=lambda: reviewed_at,
    )

    exact = memory_service.find_relevant_records(exact_query)
    similar = memory_service.find_relevant_records(similar_query)

    assert exact.matches[0].applicability_report is not None
    assert exact.matches[0].applicability_report.status.value == "applicable"
    assert exact.returned_context_only_count == 0
    assert similar.matches[0].applicability_report is not None
    assert similar.matches[0].applicability_report.status.value == "partial"
    assert similar.matches[0].applicability_report.context_only_allowed is True
    assert similar.returned_context_only_count == 1


def test_same_rule_code_and_behavior_with_different_names_form_separate_cohorts() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)

    first_name = "ElasticSearch remote code execution"
    second_name = "WebLogic deserialization remote code execution"
    first_observation = _observe(
        service,
        _run(1, rule_name=first_name),
        "batch:1",
    ).observation
    first_candidate = _observe(
        service,
        _run(2, rule_name=first_name),
        "batch:2",
    ).candidate
    second_observation = _observe(
        service,
        _run(3, rule_name=second_name),
        "batch:3",
    ).observation
    second_candidate = _observe(
        service,
        _run(4, rule_name=second_name),
        "batch:4",
    ).candidate

    assert first_candidate is not None
    assert second_candidate is not None
    assert first_observation.signature.value != second_observation.signature.value
    assert first_observation.signature.facets["detection_signature"] != second_observation.signature.facets["detection_signature"]
    assert len(repository.list_memory_candidates()) == 2


def test_weak_only_behavior_creates_context_candidate_without_decision_authority() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    weak = {
        "techniques": [],
        "network_protocol": "tcp",
        "scenario_type": "web_attack",
    }

    _observe(service, _run(1, **weak), "batch:1")
    aggregated = _observe(service, _run(2, **weak), "batch:2")

    assert aggregated.candidate is not None
    assert aggregated.observation.signature.facets["behavior_strength"] == ["weak_only"]
    assert aggregated.candidate.decision_impact is SocMemoryDecisionImpact.REVIEW_HINT
    assert aggregated.candidate.metadata["decision_scope"] == "rule_context_only"
    assert aggregated.candidate.applicability is not None
    assert set(aggregated.candidate.applicability.required_facets) == {
        "detection_key",
        "detection_signature",
        "environment",
    }


def test_context_only_requires_a_shared_strong_behavior_component() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1, techniques=["T1059", "T1071"]), "batch:1")
    aggregated = _observe(
        pattern_service,
        _run(2, techniques=["T1059", "T1071"]),
        "batch:2",
    )
    assert aggregated.candidate is not None

    reviewed_at = _START + timedelta(hours=1)
    record = (
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
            mutation_audit_repository=repository,
            now_provider=lambda: reviewed_at,
        )
        .review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=aggregated.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="Strong behavior overlap is required for partial retrieval.",
                activate_retrieval=True,
                activation_valid_until=reviewed_at + timedelta(days=30),
                activation_review_after_days=7,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                )
            ),
        )
        .memory_record
    )
    assert record is not None

    registry = build_soc_memory_profile_registry()
    unrelated_request = _run(
        3,
        techniques=["T1021", "T1047"],
    ).llm_analysis_request
    assert unrelated_request is not None
    query = memory_query_from_analysis_request(
        unrelated_request,
        profile=registry.resolve_request(unrelated_request),
    )
    result = SocMemoryService(
        record_repository=repository,
        now_provider=lambda: reviewed_at,
    ).find_relevant_records(query)

    assert result.matches == []
    assert result.skipped_not_applicable == 1


def test_pingan_profile_deduplicates_ip_entity_when_role_entity_is_available() -> None:
    request = _run(1).llm_analysis_request
    assert request is not None
    request = request.model_copy(
        update={
            "extracted_entities": ExtractedEntities(
                mentions=[
                    EntityMention(
                        kind=EntityKind.IP,
                        value="30.174.29.44",
                        key="ip:30.174.29.44",
                    )
                ]
            ),
            "fact_reconstruction": FactReconstructionResult(
                role_resolutions=[
                    RoleResolution(
                        role="attacker",
                        status=RoleResolutionStatus.CONFIRMED,
                        selected_value="30.174.29.44",
                        semantic_confidence=0.9,
                        rationale="Reviewed role fact.",
                    )
                ]
            ),
        }
    )
    profile = build_soc_memory_profile_registry().resolve_request(request)

    facets = profile.project_query_facets(request)

    assert facets["role_entity"] == ["attacker:30.174.29.44"]
    assert "entity" not in facets
