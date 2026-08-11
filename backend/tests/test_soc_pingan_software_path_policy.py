from __future__ import annotations

import json
import ntpath
from pathlib import Path

import pytest
from openpyxl import Workbook

import soc_agent.application.analysis as analysis_application
from soc_agent.automation import InMemorySocAutomationRepository
from soc_agent.contracts import (
    ActorContext,
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    AnalysisRun,
    AnalysisRunStatus,
    Decision,
    DecisionEvidenceState,
    LLMAnalysisRequest,
    ProcessEntityRef,
    ServiceRequestContext,
    SocDecisionStageKind,
    SocDecisionStageStatus,
    SocOperationalDisposition,
    TenantPolicyEvaluationStatus,
    TenantPolicyReviewEffect,
    Verdict,
)
from soc_agent.core import SocAutomationService, SocTenantPolicyEvaluationService
from soc_agent.integrations.pingan.software_path_catalog import (
    PingAnSoftwarePathCatalog,
    PingAnSoftwarePathMatchType,
    compile_pingan_software_path_catalog,
)
from soc_agent.integrations.pingan.software_path_policy import (
    PINGAN_SOFTWARE_PATH_ALL_SAFE_VALUE,
    PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL,
    PingAnSoftwarePathPolicySignalProvider,
)
from soc_agent.integrations.pingan.tenant_disposition import (
    load_pingan_tenant_disposition_policy,
)
from soc_agent.tenant_policy import (
    InMemoryTenantPolicyDecisionRepository,
    StaticTenantPolicyResolver,
    evaluate_tenant_policy,
)


def test_compiler_keeps_exact_paths_and_infers_only_safe_random_families(tmp_path: Path) -> None:
    catalog_path = tmp_path / "software-paths.sqlite"
    report = compile_pingan_software_path_catalog(
        _family_workbook(tmp_path / "software-paths.xlsx"),
        catalog_path,
    )
    catalog = PingAnSoftwarePathCatalog(catalog_path)

    assert report.path_entry_count == 8
    assert report.path_family_count == 1
    assert report.path_family_member_count == 2

    family = catalog.lookup(r"D:\ccmcache\9z\security-agent.exe")
    assert family.matched is True
    assert family.match_type is PingAnSoftwarePathMatchType.PATH_FAMILY
    assert family.exact_safe_path_candidate is False
    assert family.path_family_context is not None
    assert family.path_family_context.pattern_path == r"d:\ccmcache\{dynamic_segment}\security-agent.exe"
    assert family.path_family_context.member_path_count == 2

    other_only = catalog.lookup(r"D:\ccmcache\3c\unknown-tool.exe")
    assert other_only.matched is True
    assert other_only.exact_safe_path_candidate is False
    assert other_only.path_family_context is None

    assert catalog.lookup(r"C:\Windows\future123\cmd.exe").matched is False


def test_exact_other_paths_record_outranks_a_broader_safe_family(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "software-paths.sqlite"
    compile_pingan_software_path_catalog(
        _family_workbook(tmp_path / "software-paths.xlsx"),
        catalog_path,
    )
    run = _edr_run(r"D:\ccmcache\8x\security-agent.exe")
    policy = load_pingan_tenant_disposition_policy()
    resolution = PingAnSoftwarePathPolicySignalProvider(PingAnSoftwarePathCatalog(catalog_path)).resolve(policy, run, environment="dev")

    assert not any(signal.signal_key == PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL for signal in resolution.signals)
    assert resolution.signals[0].signal_value == "other_paths_only"


def test_path_family_signal_directly_drives_pingan_ignore_policy(tmp_path: Path) -> None:
    catalog_path = tmp_path / "software-paths.sqlite"
    compile_pingan_software_path_catalog(
        _family_workbook(tmp_path / "software-paths.xlsx"),
        catalog_path,
    )
    run = _edr_run(r"D:\ccmcache\9z\security-agent.exe")
    policy = load_pingan_tenant_disposition_policy()
    resolution = PingAnSoftwarePathPolicySignalProvider(PingAnSoftwarePathCatalog(catalog_path)).resolve(policy, run, environment="dev")

    aggregate = next(signal for signal in resolution.signals if signal.signal_key == PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL)
    assert aggregate.signal_value == PINGAN_SOFTWARE_PATH_ALL_SAFE_VALUE
    assert aggregate.attributes["path_family_match_count"] == 1

    decision = evaluate_tenant_policy(
        policy,
        run,
        environment="dev",
        signal_resolutions=[resolution],
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.MATCHED
    assert decision.selected_rule_id == "edr-safe-software-path-fast-ignore"
    assert decision.detection_truth.verdict is Verdict.SUSPICIOUS
    assert decision.recommended_disposition is SocOperationalDisposition.IGNORED
    assert decision.review_effect is TenantPolicyReviewEffect.CLEAR
    assert decision.auto_apply_allowed is True
    assert decision.policy_signal_hash is not None
    assert decision.policy_signal_resolutions == [resolution]


def test_exact_safe_path_directly_drives_same_pingan_ignore_policy(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "software-paths.sqlite"
    compile_pingan_software_path_catalog(
        _family_workbook(tmp_path / "software-paths.xlsx"),
        catalog_path,
    )
    run = _edr_run(r"D:\ccmcache\1a\security-agent.exe")
    policy = load_pingan_tenant_disposition_policy()
    resolution = PingAnSoftwarePathPolicySignalProvider(PingAnSoftwarePathCatalog(catalog_path)).resolve(policy, run, environment="dev")

    aggregate = next(signal for signal in resolution.signals if signal.signal_key == PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL)
    assert aggregate.attributes["exact_path_match_count"] == 1
    assert aggregate.attributes["path_family_match_count"] == 0

    decision = evaluate_tenant_policy(
        policy,
        run,
        environment="dev",
        signal_resolutions=[resolution],
    )
    assert decision.selected_rule_id == "edr-safe-software-path-fast-ignore"
    assert decision.recommended_disposition is SocOperationalDisposition.IGNORED


def test_exact_safe_path_hash_mismatch_cannot_fall_back_to_family(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "software-paths.sqlite"
    compile_pingan_software_path_catalog(
        _family_workbook(tmp_path / "software-paths.xlsx"),
        catalog_path,
    )
    run = _edr_run(
        r"D:\ccmcache\1a\security-agent.exe",
        md5="ffffffffffffffffffffffffffffffff",
    )
    policy = load_pingan_tenant_disposition_policy()
    resolution = PingAnSoftwarePathPolicySignalProvider(PingAnSoftwarePathCatalog(catalog_path)).resolve(policy, run, environment="dev")

    assert not any(signal.signal_key == PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL for signal in resolution.signals)
    path_signal = next(signal for signal in resolution.signals if signal.signal_key != PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL)
    assert path_signal.signal_value == "hash_mismatch"


def test_safe_path_ignore_is_persisted_in_four_stage_effective_lineage(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "software-paths.sqlite"
    compile_pingan_software_path_catalog(
        _family_workbook(tmp_path / "software-paths.xlsx"),
        catalog_path,
    )
    run = _edr_run(r"D:\ccmcache\9z\security-agent.exe")
    policy = load_pingan_tenant_disposition_policy()
    tenant_repository = InMemoryTenantPolicyDecisionRepository()
    context = ServiceRequestContext(
        actor=ActorContext(actor_id="soc-daemon", roles=["soc_daemon"]),
    )
    tenant_decision = SocTenantPolicyEvaluationService(
        policy_resolver=StaticTenantPolicyResolver([policy]),
        repository=tenant_repository,
        environment="dev",
        signal_providers=(PingAnSoftwarePathPolicySignalProvider(PingAnSoftwarePathCatalog(catalog_path)),),
    ).evaluate(run, context=context)

    assert tenant_decision is not None
    assert tenant_decision.recommended_disposition is SocOperationalDisposition.IGNORED
    assert tenant_repository.get_tenant_policy_decision(tenant_decision.decision_id) == tenant_decision

    automation_result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=None,
        environment="dev",
        tenant_policy_repository=tenant_repository,
        tenant_policy_application_enabled=True,
    ).evaluate(run, context=context)

    transition = automation_result.decision_transition
    assert [stage.stage for stage in transition.stages] == [
        SocDecisionStageKind.BASE,
        SocDecisionStageKind.MEMORY,
        SocDecisionStageKind.TENANT_POLICY,
        SocDecisionStageKind.EFFECTIVE,
    ]
    assert transition.stages[2].status is SocDecisionStageStatus.APPLIED
    assert transition.before.verdict is Verdict.SUSPICIOUS
    assert transition.after.verdict is Verdict.SUSPICIOUS
    assert transition.before.needs_review is True
    assert transition.after.needs_review is False
    assert transition.effective_disposition is SocOperationalDisposition.IGNORED
    assert automation_result.authorization is None
    assert automation_result.execution is None


def test_fast_policy_fails_closed_when_any_relevant_process_path_is_unknown(tmp_path: Path) -> None:
    catalog_path = tmp_path / "software-paths.sqlite"
    compile_pingan_software_path_catalog(
        _family_workbook(tmp_path / "software-paths.xlsx"),
        catalog_path,
    )
    run = _edr_run(r"D:\unknown\malware.exe")
    policy = load_pingan_tenant_disposition_policy()
    resolution = PingAnSoftwarePathPolicySignalProvider(PingAnSoftwarePathCatalog(catalog_path)).resolve(policy, run, environment="dev")

    assert not any(signal.signal_key == PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL for signal in resolution.signals)
    decision = evaluate_tenant_policy(
        policy,
        run,
        environment="dev",
        signal_resolutions=[resolution],
    )
    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.recommended_disposition is None


def test_fast_policy_provider_is_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED", raising=False)
    assert analysis_application.build_configured_tenant_policy_signal_providers() == ()


def test_fast_policy_provider_loads_only_from_explicit_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = tmp_path / "software-paths.sqlite"
    compile_pingan_software_path_catalog(
        _family_workbook(tmp_path / "software-paths.xlsx"),
        catalog_path,
    )
    monkeypatch.setenv("SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED", "true")
    monkeypatch.setenv("SOC_PINGAN_SOFTWARE_PATH_CATALOG_PATH", str(catalog_path))

    providers = analysis_application.build_configured_tenant_policy_signal_providers()

    assert len(providers) == 1
    assert isinstance(providers[0], PingAnSoftwarePathPolicySignalProvider)


def test_fast_policy_provider_requires_tenant_policy_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED", "true")
    monkeypatch.setenv("SOC_TENANT_POLICY_ENABLED", "false")
    monkeypatch.delenv("SOC_TENANT_DISPOSITION_POLICY_PATH", raising=False)

    with pytest.raises(
        ValueError,
        match="requires SOC_TENANT_POLICY_ENABLED=true",
    ):
        analysis_application._build_post_analysis_observers(
            None,
            settings=analysis_application.SocLLMSettings(),
        )


def _edr_run(process_path: str, *, md5: str | None = None) -> AnalysisRun:
    return AnalysisRun(
        run_id="RUN-PINGAN-SAFE-PATH-1",
        alert_id="ALERT-PINGAN-SAFE-PATH-1",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        llm_analysis_request=LLMAnalysisRequest(
            alert_id="ALERT-PINGAN-SAFE-PATH-1",
            tenant_id="pingan",
            source=AlertSourceRef(
                source_type=AlertSourceType.EDR,
                source_system="pingan-edr",
            ),
            canonical_entities=AlertEntitySet(
                process=ProcessEntityRef(
                    process_name=ntpath.basename(process_path),
                    process_path=process_path,
                    md5=md5,
                )
            ),
        ),
        decision=Decision(
            verdict=Verdict.SUSPICIOUS,
            confidence=0.7,
            evidence_state=DecisionEvidenceState.PARTIAL,
            suggested_action="Review endpoint behavior.",
            needs_review=True,
            reason="The base Runtime retains the technical detection truth.",
        ),
    )


def _family_workbook(path: Path) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["alertId", "flag", "zeusRawLogs", "inference", "path_parser"])
    rows = [
        (1001, r"D:\ccmcache\1a\security-agent.exe", "safe_paths"),
        (1002, r"D:\ccmcache\2b\security-agent.exe", "safe_paths"),
        (1003, r"D:\ccmcache\3c\unknown-tool.exe", "other_paths"),
        (1004, r"D:\ccmcache\4d\unknown-tool.exe", "other_paths"),
        (1008, r"D:\ccmcache\8x\security-agent.exe", "other_paths"),
        (1005, r"C:\Windows\System32\cmd.exe", "safe_paths"),
        (1006, r"C:\Windows\SysWOW64\cmd.exe", "safe_paths"),
        (1007, r"D:\fixed\standalone.exe", "safe_paths"),
    ]
    for alert_id, executable_path, bucket in rows:
        worksheet.append(
            [
                alert_id,
                "忽略",
                json.dumps(
                    [
                        {
                            "str_process_full": executable_path,
                            "str_process_short": executable_path.rsplit("\\", 1)[-1],
                            "str_md5": ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" if executable_path.endswith("security-agent.exe") else None),
                            "str_rule_id": "RULE-SAFE-PATH",
                            "t_detect_time": "2026-08-01 10:00:00",
                        }
                    ],
                    ensure_ascii=False,
                ),
                "{}",
                json.dumps(
                    {
                        "paths": {
                            "safe_paths": [executable_path] if bucket == "safe_paths" else [],
                            "other_paths": [executable_path] if bucket == "other_paths" else [],
                        }
                    },
                    ensure_ascii=False,
                ),
            ]
        )
    workbook.save(path)
    workbook.close()
    return path
