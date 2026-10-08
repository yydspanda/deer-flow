"""Synthetic WebShell file facts must not invent Memory process identity."""

import json

import pytest

from soc_agent.contracts import AnalysisRun, LLMAnalysisRequest
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.pipeline.extractor import extract_entities

_FILE_PATH = "/srv/synthetic-web/checkConfig.php"
_MD5 = "a" * 32
_SHA256 = "b" * 64
_HISTORICAL_IDENTITIES = {
    "7": {"profile_id": "pingan.soc", "profile_version": "7", "feature_schema_version": "pingan.soc.memory_features.v5"},
    "8": {"profile_id": "pingan.soc", "profile_version": "8", "feature_schema_version": "pingan.soc.memory_features.v6"},
    "9": {"profile_id": "pingan.soc", "profile_version": "9", "feature_schema_version": "pingan.soc.memory_features.v7"},
}


def _request(*, mode="monitor", host="synthetic-web-a", process_name=None):
    fields = {
        "datatype": "webshell",
        "event_type": "webshell",
        "event_name": "Web后门",
        "type": mode,
        "host_name": host,
        "agent_ip": "192.0.2.10" if host == "synthetic-web-a" else "192.0.2.11",
        "event_level": "high",
        "event_content": f"主机{host}的文件{_FILE_PATH}存在web后门",
        "detail.path": _FILE_PATH,
        "detail.md5": _MD5,
        "detail.sha256": _SHA256,
        "detail.level": "danger",
        "detail.hit_rule_name": "Synthetic WebShell detector",
    }
    if process_name:
        fields.update(
            {
                "detail.process_name": process_name,
                "detail.pid": "42",
                "process__file__path": "/usr/bin/" + process_name,
                "detail.cmd": process_name + " --synthetic",
            }
        )
    alert = normalize_alert_payload(
        {
            "tenant_id": "pingan",
            "alert": {
                "alertId": f"synthetic-webshell-{mode}-{host}",
                "hitLog": [{"topic": "security_qthids", "ruleCode": "synthetic-webshell", "zeusRawLogs": [{"message": json.dumps(fields, ensure_ascii=False)}]}],
            },
        }
    )
    return LLMAnalysisRequest(
        alert_id=alert.alert_id,
        tenant_id="pingan",
        environment="dev",
        source=alert.source,
        detection=alert.detection,
        classification=alert.classification,
        canonical_entities=alert.entities,
        extracted_entities=extract_entities(alert),
    )


def _project(request):
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=True)])
    profile = registry.resolve_request(request)
    facets = profile.project_query_facets(request)
    run = AnalysisRun(alert_id=request.alert_id, status="success", llm_analysis_request=request)
    signature = profile.build_pattern_signature(run, facets=facets)
    return profile, facets, signature


@pytest.mark.parametrize("mode", ["monitor", "scan"])
def test_webshell_file_only_event_has_no_process_memory_anchors(mode):
    request = _request(mode=mode)
    process = request.canonical_entities.process
    assert process.process_name is None
    assert process.process_path is None
    assert process.md5 is None
    assert process.sha256 is None
    assert process.observations == []
    assert request.extracted_entities.processes == []

    files = request.canonical_entities.file.observations
    assert len(files) == 1
    assert (files[0].file_path, files[0].md5, files[0].sha256) == (_FILE_PATH, _MD5, _SHA256)
    file_detections = [detection for detection in request.canonical_entities.detections if detection.kind == "file_detection"]
    assert file_detections
    assert all(detection.subject_refs == ["entities.file.observations[0]"] for detection in file_detections)

    profile, facets, signature = _project(request)
    core = facets["behavior_component_core"]
    assert "detected_file:checkconfig.php" in core
    assert not any("process" in component for component in core)
    assert not profile.projection_gaps(request)
    assert signature.dimension.value == "compound"


def test_webshell_scan_monitor_and_host_changes_do_not_split_file_signature():
    monitor = _request(mode="monitor", host="synthetic-web-a")
    scan = _request(mode="scan", host="synthetic-web-b")
    monitor_profile, monitor_facets, monitor_signature = _project(monitor)
    scan_profile, scan_facets, scan_signature = _project(scan)

    assert monitor.canonical_entities.host.host_name != scan.canonical_entities.host.host_name
    assert monitor.canonical_entities.host.ip_addresses != scan.canonical_entities.host.ip_addresses
    assert "detected_file:checkconfig.php" in monitor_facets["behavior_component_core"]
    assert monitor_profile.identity == scan_profile.identity
    assert monitor_facets["behavior_component_core"] == scan_facets["behavior_component_core"]
    assert monitor_facets["behavior_fingerprint"] == scan_facets["behavior_fingerprint"]
    assert monitor_signature.value == scan_signature.value


def test_explicit_webshell_process_is_retained_and_distinguishes_file_behavior():
    requests = [_request(process_name=name) for name in ("php", "python")]
    projections = []
    for request, name in zip(requests, ("php", "python"), strict=True):
        process = request.canonical_entities.process
        assert (process.process_name, process.process_id, process.process_path) == (name, 42, "/usr/bin/" + name)
        assert process.observations
        assert process.md5 is None
        assert process.sha256 is None
        assert request.canonical_entities.file.observations[0].md5 == _MD5
        assert request.canonical_entities.file.observations[0].sha256 == _SHA256
        profile, facets, signature = _project(request)
        assert "detected_file:checkconfig.php" in facets["behavior_component_core"]
        assert any(name in component and "process" in component for component in facets["behavior_component_core"])
        assert not profile.projection_gaps(request)
        projections.append((facets, signature))

    assert projections[0][0]["behavior_fingerprint"] != projections[1][0]["behavior_fingerprint"]
    assert projections[0][1].value != projections[1][1].value


@pytest.mark.parametrize("version", ["7", "8", "9"])
def test_saved_webshell_process_name_difference_remains_frozen(version):
    """A fresh normalization fix must not silently regroup historical requests."""
    saved = LLMAnalysisRequest.model_validate(
        {
            "alert_id": "synthetic-saved-webshell",
            "tenant_id": "pingan",
            "source": {"source_type": "hids", "source_system": "security_qthids", "integration_name": "pingan_legacy_alert_platform"},
            "detection": {"detection_key": "synthetic-webshell", "rule_name": "Synthetic WebShell detector"},
            "memory_profile": _HISTORICAL_IDENTITIES[version],
            "canonical_entities": {"process": {"process_path": _FILE_PATH, "md5": _MD5, "sha256": _SHA256}},
        }
    )
    named = saved.model_copy(deep=True)
    named.canonical_entities.process.process_name = "checkConfig.php"
    original_json = [request.model_dump_json() for request in (saved, named)]

    saved_profile, saved_facets, saved_signature = _project(saved)
    named_profile, named_facets, named_signature = _project(named)

    assert saved_profile.identity.profile_version == named_profile.identity.profile_version == version
    assert set(named_facets["behavior_component_core"]) - set(saved_facets["behavior_component_core"]) == {"process:checkconfig.php"}
    assert saved_facets["behavior_fingerprint"] != named_facets["behavior_fingerprint"]
    assert saved_signature.value != named_signature.value
    assert [request.model_dump_json() for request in (saved, named)] == original_json
