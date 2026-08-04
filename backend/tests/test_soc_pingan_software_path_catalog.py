from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from openpyxl import Workbook

from soc_agent.actions.adapters import SocActionAdapterRegistry
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocAgentActionCommand,
    SocAgentChatRequest,
)
from soc_agent.core import (
    InMemoryInvestigationEvidenceRepository,
    SocAgentActionDispatcher,
    SocAgentCapabilityRouter,
)
from soc_agent.integrations.pingan.software_path_catalog import (
    PINGAN_SOFTWARE_PATH_LOOKUP_ACTION,
    PingAnSoftwarePathAttention,
    PingAnSoftwarePathCatalog,
    PingAnSoftwarePathControlZone,
    PingAnSoftwarePathFreshness,
    PingAnSoftwarePathLookupActionAdapter,
    PingAnSoftwarePathMatchType,
    classify_pingan_path,
    compile_pingan_software_path_catalog,
    normalize_windows_path,
)
from soc_agent.integrations.pingan.software_path_mcp_server import _handle_message


def test_pingan_path_classification_keeps_d_drive_higher_attention() -> None:
    zone, attention = classify_pingan_path(r"D:\Program Files\Business\app.exe")
    assert zone is PingAnSoftwarePathControlZone.LESS_MANAGED
    assert attention is PingAnSoftwarePathAttention.HIGH

    zone, attention = classify_pingan_path(r"D:\Users\alice\Desktop\tool.exe")
    assert zone is PingAnSoftwarePathControlZone.USER_WRITABLE
    assert attention is PingAnSoftwarePathAttention.HIGH

    zone, attention = classify_pingan_path(r"C:\Windows\System32\cmd.exe")
    assert zone is PingAnSoftwarePathControlZone.MANAGED_SYSTEM
    assert attention is PingAnSoftwarePathAttention.LOWER


def test_catalog_compile_and_query_separate_history_from_location_risk(
    tmp_path: Path,
) -> None:
    workbook = _workbook(tmp_path / "paths.xlsx")
    catalog_path = tmp_path / "catalog.sqlite"

    report = compile_pingan_software_path_catalog(workbook, catalog_path)

    assert report.row_count == 2
    assert report.parsed_row_count == 2
    assert report.path_entry_count == 2
    assert report.observation_count == 2
    assert report.candidate_only is True
    assert report.allowlist is False
    assert report.control_zone_counts == {"less_managed": 1, "managed_system": 1}

    catalog = PingAnSoftwarePathCatalog(catalog_path, freshness_days=180)
    matched = catalog.lookup(
        r"D:/tools/psexec.exe",
        md5="0123456789ABCDEF0123456789ABCDEF",
        as_of=datetime(2025, 2, 1, tzinfo=UTC),
    )

    assert matched.matched is True
    assert matched.match_type is PingAnSoftwarePathMatchType.EXACT_PATH_AND_MD5
    assert matched.control_zone is PingAnSoftwarePathControlZone.LESS_MANAGED
    assert matched.location_attention is PingAnSoftwarePathAttention.HIGH
    assert matched.automation_eligible is False
    assert matched.decision_impact == "none"
    assert matched.historical_context is not None
    assert matched.historical_context.source_dispositions == ["忽略"]
    assert matched.historical_context.legacy_path_buckets == ["other_paths"]
    assert matched.historical_context.freshness is PingAnSoftwarePathFreshness.CURRENT
    assert matched.historical_context.process_names == ["psexec.exe"]
    assert matched.historical_context.known_md5s == ["0123456789abcdef0123456789abcdef"]
    assert any("higher-attention" in warning for warning in matched.warnings)
    assert any("not an allowlist" in warning for warning in matched.warnings)

    old_fuzzy_shape = catalog.lookup(r"D:\backup\psexec.exe")
    assert old_fuzzy_shape.matched is False
    assert old_fuzzy_shape.match_type is PingAnSoftwarePathMatchType.NONE


def test_catalog_surfaces_hash_mismatch_and_stale_history(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    compile_pingan_software_path_catalog(_workbook(tmp_path / "paths.xlsx"), catalog_path)
    catalog = PingAnSoftwarePathCatalog(catalog_path, freshness_days=30)

    result = catalog.lookup(
        r"D:\tools\psexec.exe",
        md5="ffffffffffffffffffffffffffffffff",
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result.match_type is PingAnSoftwarePathMatchType.EXACT_PATH_HASH_MISMATCH
    assert result.historical_context is not None
    assert result.historical_context.freshness is PingAnSoftwarePathFreshness.STALE
    assert any("differs" in warning for warning in result.warnings)
    assert any("stale" in warning for warning in result.warnings)


def test_action_dispatcher_persists_lookup_as_investigation_evidence(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    compile_pingan_software_path_catalog(_workbook(tmp_path / "paths.xlsx"), catalog_path)
    adapter = PingAnSoftwarePathLookupActionAdapter(PingAnSoftwarePathCatalog(catalog_path))
    registry = SocActionAdapterRegistry([adapter])
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    dispatcher = SocAgentActionDispatcher(
        action_adapter_registry=registry,
        evidence_repository=evidence_repository,
    )
    request = SocAgentChatRequest(
        message="lookup historical software path context",
        thread_id="THR-PATH-1",
        run_id="RUN-PATH-1",
        allowed_routes=[PINGAN_SOFTWARE_PATH_LOOKUP_ACTION],
        metadata={
            "soc_route": PINGAN_SOFTWARE_PATH_LOOKUP_ACTION,
            "action_payload": {
                "path": r"D:\tools\psexec.exe",
                "context_refs": {
                    "thread_id": "THR-PATH-1",
                    "run_id": "RUN-PATH-1",
                    "alert_id": "ALT-PATH-1",
                },
            },
        },
    )
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-path-test",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
        )
    )
    route = SocAgentCapabilityRouter(allowed_routes={PINGAN_SOFTWARE_PATH_LOOKUP_ACTION}).route(request)
    permission = dispatcher.check_permission(request, route, context=context)

    result = dispatcher.dispatch(
        request,
        route,
        context=context,
        permission_decision=permission,
    )

    assert result.status == "success"
    assert result.payload["matched"] is True
    assert result.payload["decision_impact"] == "none"
    assert result.payload["automation_eligible"] is False
    evidence = evidence_repository.list_evidence(thread_id="THR-PATH-1")
    assert len(evidence) == 1
    assert evidence[0].route == PINGAN_SOFTWARE_PATH_LOOKUP_ACTION
    assert evidence[0].result_payload["control_zone"] == "less_managed"
    assert evidence[0].mocked is False


def test_action_adapter_dry_run_does_not_read_catalog(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    compile_pingan_software_path_catalog(_workbook(tmp_path / "paths.xlsx"), catalog_path)
    adapter = PingAnSoftwarePathLookupActionAdapter(PingAnSoftwarePathCatalog(catalog_path))

    result = adapter.dry_run(
        SocAgentActionCommand(
            route=PINGAN_SOFTWARE_PATH_LOOKUP_ACTION,
            action=PINGAN_SOFTWARE_PATH_LOOKUP_ACTION,
            dry_run=True,
            payload={"path": r"D:\tools\psexec.exe"},
        ),
        context=ServiceRequestContext(),
    )

    assert result.payload["external_side_effect"] == "not_executed"
    assert "matched" not in result.payload


def test_mcp_returns_structured_investigation_only_result(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    compile_pingan_software_path_catalog(_workbook(tmp_path / "paths.xlsx"), catalog_path)
    catalog = PingAnSoftwarePathCatalog(catalog_path)

    response = _handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "software_path_lookup",
                "arguments": {"path": r"D:\tools\psexec.exe"},
            },
        },
        catalog=catalog,
        startup_error=None,
    )

    assert response is not None
    structured = response["result"]["structuredContent"]
    assert structured["matched"] is True
    assert structured["candidate_only"] is True
    assert structured["allowlist"] is False
    assert structured["evidence_boundary"] == "investigation_only"
    assert structured["decision_impact"] == "none"
    assert structured["automation_eligible"] is False


def test_windows_path_normalization_does_not_enable_old_segment_fuzzing() -> None:
    assert normalize_windows_path(r"D:/Apps/Tool.EXE") == r"d:\apps\tool.exe"
    assert normalize_windows_path(r"D:\Apps\1.2\tool.exe") != normalize_windows_path(r"D:\Apps\2.0\tool.exe")


def _workbook(path: Path) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["alertId", "flag", "zeusRawLogs", "inference", "path_parser"])
    worksheet.append(
        [
            1001,
            "忽略",
            json.dumps(
                [
                    {
                        "str_process_full": r"D:\tools\psexec.exe",
                        "str_process_short": "psexec.exe",
                        "str_md5": "0123456789ABCDEF0123456789ABCDEF",
                        "str_rule_id": "RULE-D-1",
                        "t_detect_time": "2025-01-15 10:00:00",
                    }
                ],
                ensure_ascii=False,
            ),
            "{}",
            json.dumps(
                {
                    "paths": {
                        "safe_paths": [],
                        "other_paths": [r"D:\tools\psexec.exe"],
                    }
                },
                ensure_ascii=False,
            ),
        ]
    )
    worksheet.append(
        [
            1002,
            "忽略",
            json.dumps(
                [
                    {
                        "str_process_full": r"C:\Windows\System32\cmd.exe",
                        "str_process_short": "cmd.exe",
                        "str_md5": "abcdef0123456789abcdef0123456789",
                        "str_rule_id": "RULE-C-1",
                        "t_detect_time": "2025-01-16 10:00:00",
                    }
                ],
                ensure_ascii=False,
            ),
            "{}",
            json.dumps(
                {
                    "paths": {
                        "safe_paths": [r"C:\Windows\System32\cmd.exe"],
                        "other_paths": [],
                    }
                },
                ensure_ascii=False,
            ),
        ]
    )
    workbook.save(path)
    workbook.close()
    return path
