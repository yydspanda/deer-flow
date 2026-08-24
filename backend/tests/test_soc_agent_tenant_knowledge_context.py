from __future__ import annotations

import pytest
from pydantic import ValidationError

from soc_agent.contracts import (
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    DetectionRuleRef,
    ExtractedEntities,
    FileEntityRef,
    FileObservationRef,
    FileObservationRelation,
    HostEntityRef,
    HttpEntityRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ProcessEntityRef,
    ProcessNodeRef,
    ProcessObservationRef,
    TenantKnowledgeFact,
    TenantKnowledgeSelector,
    UserEntityRef,
)
from soc_agent.integrations.pingan.knowledge import (
    load_pingan_endpoint_playbooks_profile,
    load_pingan_internal_systems_profile,
    load_pingan_network_direction_profile,
    load_pingan_platform_context_profile,
    load_pingan_tenant_knowledge_profiles,
)
from soc_agent.knowledge import TenantKnowledgeAnalysisRequestEnricher
from soc_agent.pipeline.reference_catalog import finalize_analysis_reference_catalogs


def _request(
    *,
    integration_name: str = "pingan_legacy_alert_platform",
    source_type: AlertSourceType = AlertSourceType.NDR,
    canonical_entities: AlertEntitySet | None = None,
    extracted_entities: ExtractedEntities | None = None,
    rule_name: str = "发现反弹SHELL行为（Linux）",
) -> LLMAnalysisRequest:
    return LLMAnalysisRequest(
        alert_id="ALT-DIRECTION-CONTEXT-1",
        tenant_id="pingan",
        source=AlertSourceRef(
            source_type=source_type,
            source_system="zeus",
            integration_name=integration_name,
        ),
        detection=DetectionRuleRef(rule_name=rule_name),
        canonical_entities=canonical_entities or AlertEntitySet(network=NetworkEntityRef(source_ip="30.116.114.150", destination_ip="30.174.29.44")),
        extracted_entities=extracted_entities or ExtractedEntities(ips=["30.116.114.150", "30.174.29.44"]),
    )


def test_pingan_direction_knowledge_projects_only_relevant_context() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])

    request = finalize_analysis_reference_catalogs(enricher(_request()))

    context = {item.metadata.get("fact_id"): item for item in request.context_catalog}
    assert "pa.internal-address-space" in context
    assert "pa.network-direction-method" in context
    assert "pa.reverse-connection-role-inversion" in context
    assert "pa.internal-geoip-enrichment-caveat" in context
    assert "pa.proxy-cdn-client-chain" not in context
    assert context["pa.internal-address-space"].context_ref.startswith("C-")
    assert context["pa.internal-address-space"].metadata["matched_values"] == {"cidrs": ["30.116.114.150", "30.174.29.44"]}
    assert "10.0.0.0/8" not in context["pa.internal-address-space"].summary
    assert context["pa.internal-address-space"].metadata["decision_authority"] == "none"
    assert context["pa.internal-address-space"].metadata["network_scope_membership"] == "organization_controlled"
    assert "must not override" in context["pa.internal-geoip-enrichment-caveat"].summary


def test_pingan_geoip_caveat_is_scoped_to_confirmed_30_network() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(source_ip="26.1.2.3")),
        extracted_entities=ExtractedEntities(ips=["26.1.2.3"]),
        rule_name="generic network event",
    )

    fact_ids = {item.metadata["fact_id"] for item in enricher(request).context_catalog}

    assert "pa.internal-address-space" in fact_ids
    assert "pa.internal-geoip-enrichment-caveat" not in fact_ids


def test_tenant_profile_does_not_leak_into_another_integration() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher(load_pingan_tenant_knowledge_profiles())

    request = enricher(_request(integration_name="another_vendor"))

    assert request.context_catalog == []


@pytest.mark.parametrize("address", ["26.1.2.3", "29.4.5.6", "172.31.9.8"])
def test_confirmed_pingan_internal_ranges_are_projected(address: str) -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(source_ip=address)),
        extracted_entities=ExtractedEntities(ips=[address]),
        rule_name="generic network event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert context["pa.internal-address-space"].metadata["matched_values"] == {"cidrs": [address]}


def test_office_subnet_refines_but_does_not_replace_internal_ownership() -> None:
    address = "10.107.11.132"
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(source_ip=address)),
        extracted_entities=ExtractedEntities(ips=[address]),
        rule_name="generic endpoint event",
    )

    fact_ids = {item.metadata["fact_id"] for item in enricher(request).context_catalog}

    assert {"pa.internal-address-space", "pa.office-address-space"} <= fact_ids


def test_public_corporate_domain_projects_negative_direction_caveat() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(http=HttpEntityRef(host="www.pingan.com.cn")),
        extracted_entities=ExtractedEntities(domains=["www.pingan.com.cn"]),
        rule_name="generic web event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert "pa.public-corporate-domain-caveat" in context
    assert "must not be used as proof" in context["pa.public-corporate-domain-caveat"].summary


def test_typed_internal_system_selectors_use_canonical_entities() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_internal_systems_profile()])
    request = _request(
        source_type=AlertSourceType.EDR,
        canonical_entities=AlertEntitySet(
            host=HostEntityRef(host_name="CTXGMPVS-PA178"),
            process=ProcessEntityRef(
                process_name=r"C:\Program Files\pingantechmail\B\PaMailH5App.exe",
                process_path=r"C:\Program Files\pingantechmail\B\PaMailH5App.exe",
            ),
            user=UserEntityRef(um_account="EX-ZHANGWU233"),
            http=HttpEntityRef(path="/pws/askbob-gpt/chat/completions"),
        ),
        extracted_entities=ExtractedEntities(),
        rule_name="generic endpoint event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert {
        "pa.ctx-cloud-desktop-host",
        "pa.pamail-client-process",
        "pa.pamail-install-path",
        "pa.askbob-llm-endpoint",
        "pa.domain-account-convention",
    } <= context.keys()
    assert context["pa.ctx-cloud-desktop-host"].metadata["matched_values"] == {"host_prefixes": ["ctxgmpvs-pa178"]}
    assert context["pa.pamail-client-process"].metadata["matched_values"] == {"process_names": ["pamailh5app.exe"]}
    assert context["pa.domain-account-convention"].metadata["decision_authority"] == "none"


def test_typed_selectors_do_not_match_terms_only_present_in_detection_text() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_internal_systems_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(),
        extracted_entities=ExtractedEntities(),
        rule_name=("mentions CTXGMPVS-PA178 PaMailH5App.exe EX-ZHANGWU233 /pws/askbob-gpt but has no typed entities"),
    )

    assert enricher(request).context_catalog == []


def test_multi_signal_application_identity_requires_every_selector_group() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_internal_systems_profile()])
    partial = _request(
        source_type=AlertSourceType.HIDS,
        canonical_entities=AlertEntitySet(process=ProcessEntityRef(process_name="ubiops-agent")),
        extracted_entities=ExtractedEntities(),
        rule_name="generic process event",
    )
    complete = partial.model_copy(
        update={
            "canonical_entities": AlertEntitySet(
                process=ProcessEntityRef(
                    process_name="ubiops-agent",
                    process_path="/tmp/ubiops-agent/install.sh",
                )
            )
        }
    )

    assert "pa.ubiops-agent-installation" not in {item.metadata["fact_id"] for item in enricher(partial).context_catalog}
    assert "pa.ubiops-agent-installation" in {item.metadata["fact_id"] for item in enricher(complete).context_catalog}


def test_pingan_group_policy_playbook_requires_typed_process_and_command_signals() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])
    partial = _request(
        source_type=AlertSourceType.EDR,
        canonical_entities=AlertEntitySet(
            process=ProcessEntityRef(
                observations=[
                    ProcessObservationRef(
                        observation_id="process-1",
                        evidence_path="entities.process.observations[0]",
                        nodes=[
                            ProcessNodeRef(
                                process_name="gpscript.exe",
                                command_line="gpscript.exe /Logon",
                            ),
                            ProcessNodeRef(
                                process_name="powershell.exe",
                                command_line="powershell.exe -File unrelated.ps1",
                            ),
                        ],
                    )
                ]
            )
        ),
        extracted_entities=ExtractedEntities(processes=["gpscript.exe", "powershell.exe"]),
        rule_name="GalaxyLab_T1059-Powershell-Execution mentions Map_Drive.ps1",
    )
    complete = partial.model_copy(
        update={
            "canonical_entities": AlertEntitySet(
                process=ProcessEntityRef(
                    observations=[
                        ProcessObservationRef(
                            observation_id="process-1",
                            evidence_path="entities.process.observations[0]",
                            nodes=[
                                ProcessNodeRef(
                                    process_name="gpscript.exe",
                                    command_line="gpscript.exe /Logon",
                                ),
                                ProcessNodeRef(
                                    process_name="powershell.exe",
                                    command_line="powershell.exe -ExecutionPolicy ByPass -File Map_Drive.ps1",
                                ),
                            ],
                        )
                    ]
                )
            )
        }
    )

    assert enricher(partial).context_catalog == []
    context = {item.metadata["fact_id"]: item for item in enricher(complete).context_catalog}
    assert context["pa.endpoint-group-policy-logon-script"].metadata["matched_values"] == {
        "command_terms": ["map_drive.ps1"],
        "process_names": ["gpscript.exe"],
        "source_types": ["edr"],
    }
    assert context["pa.endpoint-group-policy-logon-script"].metadata["decision_authority"] == "none"


def test_pingan_sccm_playbook_requires_one_complete_process_observation() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])
    split_observations = _request(
        source_type=AlertSourceType.EDR,
        canonical_entities=AlertEntitySet(
            process=ProcessEntityRef(
                observations=[
                    ProcessObservationRef(
                        observation_id="process-parent",
                        event_scope_id="event-split",
                        evidence_path="entities.process.observations[0]",
                        nodes=[
                            ProcessNodeRef(
                                process_name="Ccm32BitLauncher.exe",
                                process_path=r"C:\Windows\CCM\Ccm32BitLauncher.exe",
                            ),
                            ProcessNodeRef(process_name="cmd.exe"),
                        ],
                    ),
                    ProcessObservationRef(
                        observation_id="process-child",
                        event_scope_id="event-split",
                        evidence_path="entities.process.observations[1]",
                        nodes=[
                            ProcessNodeRef(process_name="cmd.exe"),
                            ProcessNodeRef(
                                process_name="powershell.exe",
                                command_line=("powershell.exe -ExecutionPolicy Bypass -windowstyle hidden -file start.ps1"),
                            ),
                        ],
                    ),
                ]
            )
        ),
        extracted_entities=ExtractedEntities(processes=["Ccm32BitLauncher.exe", "powershell.exe"]),
        rule_name="generic endpoint event",
    )
    complete_observation = split_observations.model_copy(
        update={
            "canonical_entities": AlertEntitySet(
                process=ProcessEntityRef(
                    observations=[
                        ProcessObservationRef(
                            observation_id="process-complete",
                            evidence_path="entities.process.observations[0]",
                            nodes=[
                                ProcessNodeRef(
                                    process_name="Ccm32BitLauncher.exe",
                                    process_path=(r"C:\Windows\CCM\Ccm32BitLauncher.exe"),
                                ),
                                ProcessNodeRef(
                                    process_name="powershell.exe",
                                    command_line=("powershell.exe -ExecutionPolicy Bypass -windowstyle hidden -file start.ps1"),
                                ),
                            ],
                        )
                    ]
                )
            )
        }
    )

    split_ids = {item.metadata["fact_id"] for item in enricher(split_observations).context_catalog}
    assert "pa.endpoint-sccm-powershell-deployment" not in split_ids
    context = {item.metadata["fact_id"]: item for item in enricher(complete_observation).context_catalog}
    matches = context["pa.endpoint-sccm-powershell-deployment"].metadata["matched_values"]
    assert matches["process_observation_patterns"] == [("process-complete|process_names=ccm32bitlauncher.exe,powershell.exe|command_terms=-executionpolicy bypass,-windowstyle hidden,-file start.ps1|path_prefixes=c:/windows/ccm")]
    assert context["pa.endpoint-sccm-powershell-deployment"].metadata["decision_authority"] == "none"


def test_pingan_sccm_playbook_accepts_ccmcache_install_script_variant() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])
    request = _request(
        source_type=AlertSourceType.EDR,
        canonical_entities=AlertEntitySet(
            process=ProcessEntityRef(
                observations=[
                    ProcessObservationRef(
                        observation_id="process-install",
                        evidence_path="entities.process.observations[0]",
                        nodes=[
                            ProcessNodeRef(
                                process_name="Ccm32BitLauncher.exe",
                                process_path=r"C:\Windows\CCM\Ccm32BitLauncher.exe",
                            ),
                            ProcessNodeRef(
                                process_name="powershell.exe",
                                command_line=(
                                    r"powershell.exe -NoProfile -ExecutionPolicy bypass "
                                    r"-windowstyle hidden -file C:\Windows\ccmcache\1y\install.ps1"
                                ),
                            ),
                        ],
                    )
                ]
            )
        ),
        extracted_entities=ExtractedEntities(processes=["Ccm32BitLauncher.exe", "powershell.exe"]),
        rule_name="generic endpoint event",
    )

    fact_ids = {item.metadata["fact_id"] for item in enricher(request).context_catalog}

    assert "pa.endpoint-sccm-powershell-deployment" in fact_ids


def test_process_pattern_matches_connected_edges_in_one_event_scope() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])
    request = _request(
        source_type=AlertSourceType.EDR,
        canonical_entities=AlertEntitySet(
            process=ProcessEntityRef(
                observations=[
                    ProcessObservationRef(
                        observation_id="process-edge-parent",
                        event_scope_id="event-sccm-1",
                        evidence_path="entities.process.observations[0]",
                        nodes=[
                            ProcessNodeRef(
                                process_name="Ccm32BitLauncher.exe",
                                process_id=100,
                                process_path=r"C:\Windows\CCM\Ccm32BitLauncher.exe",
                            ),
                            ProcessNodeRef(
                                process_name="cmd.exe",
                                process_id=200,
                            ),
                        ],
                    ),
                    ProcessObservationRef(
                        observation_id="process-edge-child",
                        event_scope_id="event-sccm-1",
                        evidence_path="entities.process.observations[1]",
                        nodes=[
                            ProcessNodeRef(
                                process_name="cmd.exe",
                                process_id=200,
                            ),
                            ProcessNodeRef(
                                process_name="powershell.exe",
                                process_id=300,
                                command_line=(
                                    r"powershell.exe -NoProfile -ExecutionPolicy bypass "
                                    r"-windowstyle hidden -file C:\Windows\ccmcache\1y\install.ps1"
                                ),
                            ),
                        ],
                    ),
                ]
            )
        ),
        extracted_entities=ExtractedEntities(processes=["Ccm32BitLauncher.exe", "cmd.exe", "powershell.exe"]),
        rule_name="generic endpoint event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert "pa.endpoint-sccm-powershell-deployment" in context
    assert context["pa.endpoint-sccm-powershell-deployment"].metadata["matched_values"]["process_observation_patterns"][0].startswith("event-sccm-1")


def test_pingan_pycharm_wmic_playbook_ignores_rule_text_without_typed_command() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])
    partial = _request(
        source_type=AlertSourceType.HIDS,
        canonical_entities=AlertEntitySet(
            process=ProcessEntityRef(
                observations=[
                    ProcessObservationRef(
                        observation_id="process-pycharm",
                        evidence_path="entities.process.observations[0]",
                        nodes=[
                            ProcessNodeRef(process_name="pycharm64.exe"),
                            ProcessNodeRef(process_name="WMIC.exe"),
                        ],
                    )
                ]
            )
        ),
        extracted_entities=ExtractedEntities(processes=["pycharm64.exe", "WMIC.exe"]),
        rule_name=("wmic SecurityCenter2 AntivirusProduct Get displayName,productState"),
    )
    complete = partial.model_copy(
        update={
            "canonical_entities": AlertEntitySet(
                process=ProcessEntityRef(
                    observations=[
                        ProcessObservationRef(
                            observation_id="process-pycharm",
                            evidence_path="entities.process.observations[0]",
                            nodes=[
                                ProcessNodeRef(process_name="pycharm64.exe"),
                                ProcessNodeRef(
                                    process_name="WMIC.exe",
                                    command_line=(
                                        r"wmic /Namespace:\\root\SecurityCenter2 "
                                        "Path AntivirusProduct Get "
                                        "displayName,productState"
                                    ),
                                ),
                            ],
                        )
                    ]
                )
            )
        }
    )

    partial_ids = {item.metadata["fact_id"] for item in enricher(partial).context_catalog}
    assert "pa.endpoint-pycharm-wmic-av-inventory" not in partial_ids
    complete_ids = {item.metadata["fact_id"] for item in enricher(complete).context_catalog}
    assert "pa.endpoint-pycharm-wmic-av-inventory" in complete_ids


@pytest.mark.parametrize(
    ("process_name", "process_path"),
    [
        ("notepad++port.exe", r"D:\Program Files (x86)\notepad++port.exe"),
        ("notepad++.exe", r"D:\software\Notepad7.6\Notepad\notepad++.exe"),
    ],
)
def test_pingan_notepad_memory_map_playbook_requires_interactive_known_path(
    process_name: str,
    process_path: str,
) -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])
    request = _request(
        source_type=AlertSourceType.EDR,
        canonical_entities=AlertEntitySet(
            process=ProcessEntityRef(
                process_name=process_name,
                process_path=process_path,
                parent_process_name="explorer.exe",
                observations=[
                    ProcessObservationRef(
                        observation_id="process-notepad",
                        evidence_path="entities.process.observations[0]",
                        nodes=[
                            ProcessNodeRef(
                                process_name=process_name,
                                process_path=process_path,
                            )
                        ],
                    )
                ],
            )
        ),
        extracted_entities=ExtractedEntities(processes=[process_name, "explorer.exe"]),
        rule_name="generic endpoint event",
    )
    non_interactive = request.model_copy(
        update={
            "canonical_entities": request.canonical_entities.model_copy(update={"process": request.canonical_entities.process.model_copy(update={"parent_process_name": "powershell.exe"})}),
            "extracted_entities": ExtractedEntities(processes=[process_name, "powershell.exe"]),
        }
    )

    assert "pa.endpoint-notepad-memory-map" in {item.metadata["fact_id"] for item in enricher(request).context_catalog}
    assert "pa.endpoint-notepad-memory-map" not in {item.metadata["fact_id"] for item in enricher(non_interactive).context_catalog}


def test_pingan_net_share_playbook_requires_exact_read_only_command() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])

    def request_for(command_line: str) -> LLMAnalysisRequest:
        return _request(
            source_type=AlertSourceType.HIDS,
            canonical_entities=AlertEntitySet(
                process=ProcessEntityRef(
                    process_name="net.exe",
                    command_line=command_line,
                    observations=[
                        ProcessObservationRef(
                            observation_id="process-net",
                            evidence_path="entities.process.observations[0]",
                            nodes=[
                                ProcessNodeRef(
                                    process_name="net.exe",
                                    command_line=command_line,
                                )
                            ],
                        )
                    ],
                )
            ),
            extracted_entities=ExtractedEntities(processes=["net.exe"]),
            rule_name="generic endpoint event",
        )

    read_only_ids = {item.metadata["fact_id"] for item in enricher(request_for("net share")).context_catalog}
    delete_ids = {item.metadata["fact_id"] for item in enricher(request_for("net share d$ /delete")).context_catalog}

    assert "pa.endpoint-net-share-list" in read_only_ids
    assert "pa.endpoint-net-share-list" not in delete_ids


def test_pingan_fdmee_playbook_requires_reviewed_unc_script_shape() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])

    def request_for(command_line: str) -> LLMAnalysisRequest:
        return _request(
            source_type=AlertSourceType.HIDS,
            canonical_entities=AlertEntitySet(
                process=ProcessEntityRef(
                    process_name="cscript.exe",
                    command_line=command_line,
                    observations=[
                        ProcessObservationRef(
                            observation_id="process-fdmee",
                            evidence_path="entities.process.observations[0]",
                            nodes=[
                                ProcessNodeRef(
                                    process_name="cscript.exe",
                                    command_line=command_line,
                                )
                            ],
                        )
                    ],
                )
            ),
            extracted_entities=ExtractedEntities(processes=["cscript.exe"]),
            rule_name="generic remote script event",
        )

    reviewed = request_for(
        r"cscript \\215.22.0.180\hfm_core_id319252_vol1002_prd"
        r"\FDMEEWorkspace\PAHFM\data\scripts\event\AftValidate.vbs 251851 token"
    )
    wrong_server = request_for(
        r"cscript \\10.1.2.3\hfm_core_id319252_vol1002_prd"
        r"\FDMEEWorkspace\PAHFM\data\scripts\event\AftValidate.vbs 251851 token"
    )
    wrong_script_area = request_for(
        r"cscript \\215.22.0.180\hfm_core_id319252_vol1002_prd"
        r"\FDMEEWorkspace\PAHFM\downloads\payload.vbs"
    )

    reviewed_context = {item.metadata["fact_id"]: item for item in enricher(reviewed).context_catalog}
    assert "pa.endpoint-fdmee-unc-script" in reviewed_context
    assert reviewed_context["pa.endpoint-fdmee-unc-script"].metadata["decision_authority"] == "none"
    assert "pa.endpoint-fdmee-unc-script" not in {item.metadata["fact_id"] for item in enricher(wrong_server).context_catalog}
    assert "pa.endpoint-fdmee-unc-script" not in {item.metadata["fact_id"] for item in enricher(wrong_script_area).context_catalog}


def test_pingan_office_assistant_nsis_playbook_requires_product_chain_and_plugin() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])

    def request_for(
        *,
        product_directory: str = "AI Office Assistant",
        plugin_name: str = "System.dll",
        plugin_path: str = r"C:\Windows\Temp\nssE773.tmp\System.dll",
    ) -> LLMAnalysisRequest:
        return _request(
            source_type=AlertSourceType.EDR,
            canonical_entities=AlertEntitySet(
                process=ProcessEntityRef(
                    process_name="old-uninstaller.exe",
                    process_path=(r"C:\Windows\Temp\nsmCD53.tmp\old-uninstaller.exe"),
                    command_line=(
                        r"C:\Windows\Temp\nsmCD53.tmp\old-uninstaller.exe "
                        rf"/S /KEEP_APP_DATA /allusers --updated _?=C:\Program Files\{product_directory}"
                    ),
                    observations=[
                        ProcessObservationRef(
                            observation_id="process-office-assistant",
                            evidence_path="entities.process.observations[0]",
                            nodes=[
                                ProcessNodeRef(
                                    process_name="Office Assistant Setup 1.1.8.exe",
                                    process_id=2400,
                                    process_path=(r"C:\Windows\ccmcache\1l\Office Assistant Setup 1.1.8.exe"),
                                ),
                                ProcessNodeRef(
                                    process_name="old-uninstaller.exe",
                                    process_id=656,
                                    process_path=(r"C:\Windows\Temp\nsmCD53.tmp\old-uninstaller.exe"),
                                    command_line=(
                                        r"C:\Windows\Temp\nsmCD53.tmp\old-uninstaller.exe "
                                        rf"/S /KEEP_APP_DATA /allusers --updated _?=C:\Program Files\{product_directory}"
                                    ),
                                ),
                            ],
                        )
                    ],
                ),
                file=FileEntityRef(
                    observations=[
                        FileObservationRef(
                            observation_id="file-office-assistant-plugin",
                            evidence_path="entities.file.observations[0]",
                            relation=FileObservationRelation.ENDPOINT_ACTION_TARGET,
                            process_id=656,
                            file_name=plugin_name,
                            file_path=plugin_path,
                        )
                    ]
                ),
            ),
            extracted_entities=ExtractedEntities(processes=["Office Assistant Setup 1.1.8.exe", "old-uninstaller.exe"]),
            rule_name="generic endpoint event",
        )

    reviewed = request_for()
    another_product = request_for(product_directory="Another Product")
    another_plugin = request_for(
        plugin_name="payload.dll",
        plugin_path=r"C:\Windows\Temp\nssE773.tmp\payload.dll",
    )

    reviewed_context = {item.metadata["fact_id"]: item for item in enricher(reviewed).context_catalog}
    assert "pa.endpoint-office-assistant-nsis-update" in reviewed_context
    assert reviewed_context["pa.endpoint-office-assistant-nsis-update"].metadata["decision_authority"] == "none"
    assert "pa.endpoint-office-assistant-nsis-update" not in {item.metadata["fact_id"] for item in enricher(another_product).context_catalog}
    assert "pa.endpoint-office-assistant-nsis-update" not in {item.metadata["fact_id"] for item in enricher(another_plugin).context_catalog}


@pytest.mark.parametrize(
    ("process_path", "expected"),
    [
        (r"C:\Windows\SysWOW64\msiexec.exe", True),
        (r"C:\Windows\System32\msiexec.exe", True),
        (r"C:\Users\public\msiexec.exe", False),
    ],
)
def test_pingan_msi_startup_playbook_requires_complete_install_context(
    process_path: str,
    expected: bool,
) -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_endpoint_playbooks_profile()])

    def request_for(
        *,
        command_line: str = (r"C:\Windows\SysWOW64\msiexec.exe -Embedding GUID E Global\MSI0000"),
        parent_process_name: str = "msiexec.exe",
        parent_command_line: str = r"C:\Windows\System32\msiexec.exe /V",
        artifact_path: str = (r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\Error Recovery Guide.lnk"),
    ) -> LLMAnalysisRequest:
        return _request(
            source_type=AlertSourceType.EDR,
            canonical_entities=AlertEntitySet(
                process=ProcessEntityRef(
                    process_name="msiexec.exe",
                    process_path=process_path,
                    command_line=command_line,
                    parent_process_name=parent_process_name,
                    parent_command_line=parent_command_line,
                    observations=[
                        ProcessObservationRef(
                            observation_id="process-msi",
                            evidence_path="entities.process.observations[0]",
                            nodes=[
                                ProcessNodeRef(
                                    process_name="msiexec.exe",
                                    process_path=process_path,
                                    command_line=command_line,
                                )
                            ],
                        )
                    ],
                ),
                file=FileEntityRef(
                    observations=[
                        FileObservationRef(
                            observation_id="file-msi-startup",
                            evidence_path="entities.file.observations[0]",
                            relation=FileObservationRelation.OBSERVED_ARTIFACT,
                            file_name=artifact_path.rsplit("\\", 1)[-1],
                            file_path=artifact_path,
                        )
                    ]
                ),
            ),
            extracted_entities=ExtractedEntities(processes=["msiexec.exe"]),
            rule_name="generic startup event",
        )

    complete = request_for()
    fact_ids = {item.metadata["fact_id"] for item in enricher(complete).context_catalog}
    assert ("pa.endpoint-msi-startup-shortcut" in fact_ids) is expected

    if expected:
        for incomplete in (
            request_for(command_line=r"C:\Windows\SysWOW64\msiexec.exe /i package.msi"),
            request_for(parent_process_name="powershell.exe"),
            request_for(parent_command_line=r"C:\Windows\System32\msiexec.exe /i package.msi"),
            request_for(artifact_path=r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\payload.exe"),
            request_for(artifact_path=r"C:\Users\Public\Desktop\Error Recovery Guide.lnk"),
        ):
            assert "pa.endpoint-msi-startup-shortcut" not in {item.metadata["fact_id"] for item in enricher(incomplete).context_catalog}


def test_codepilot_uri_identity_does_not_require_raw_text_search() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_internal_systems_profile()])
    request = _request(
        source_type=AlertSourceType.NIDS,
        canonical_entities=AlertEntitySet(http=HttpEntityRef(url="https://wizard.internal/code_pilot/api/v1/chat/completions?stream=true")),
        extracted_entities=ExtractedEntities(),
        rule_name="generic web event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert context["pa.codepilot-endpoint"].metadata["matched_values"] == {"uri_prefixes": ["/code_pilot/api/v1/chat/completions"]}
    assert context["pa.codepilot-endpoint"].metadata["decision_authority"] == "none"


def test_hids_platform_context_rejects_topic_based_environment_inference() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_platform_context_profile()])
    request = _request(
        source_type=AlertSourceType.HIDS,
        canonical_entities=AlertEntitySet(),
        extracted_entities=ExtractedEntities(),
        rule_name="generic host event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert "pa.hids-qingteng-source-context" in context
    assert "pa.hids-topic-does-not-prove-environment" in context
    assert "Do not infer development, staging, or production" in context["pa.hids-topic-does-not-prove-environment"].summary


def test_account_selector_rejects_invalid_regex() -> None:
    with pytest.raises(ValidationError, match="invalid tenant knowledge account pattern"):
        TenantKnowledgeSelector(account_patterns=["["])


def test_network_scope_fact_requires_explicit_membership_semantics() -> None:
    common = {
        "fact_id": "test.network-scope",
        "label": "Test scope",
        "statement": "Typed test scope.",
        "selector": {"cidrs": ["10.0.0.0/8"]},
        "source_ref": "test fixture",
    }

    with pytest.raises(ValidationError, match="require network_scope_membership"):
        TenantKnowledgeFact.model_validate({**common, "kind": "network_scope"})
    with pytest.raises(ValidationError, match="other fact kinds forbid it"):
        TenantKnowledgeFact.model_validate(
            {
                **common,
                "kind": "platform_context",
                "network_scope_membership": "organization_controlled",
            }
        )
