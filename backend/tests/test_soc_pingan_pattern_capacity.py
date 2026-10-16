"""Oversized frozen PingAn features must not widen or truncate a learned scope."""

import pytest
from pydantic import ValidationError
from test_soc_pingan_memory_profile import _run

from soc_agent.contracts import AlertEntitySet, AlertSourceType, MemoryPatternDataClass, MemoryPatternSourceType, ProcessEntityRef
from soc_agent.integrations.pingan.memory import profile as profile_module
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory import memory_query_from_analysis_request
from soc_agent.memory.patterns import MemoryPatternIneligibleError, memory_pattern_command_from_run


def _profile():
    return PingAnSocMemoryProfile(semantic_features=True, directional_services=False)


def _process_run(path, index=1):
    run = _run(
        index,
        source_type=AlertSourceType.EDR,
        product="edr",
        techniques=["T1557.001"],
        canonical_entities=AlertEntitySet(process=ProcessEntityRef(process_name="worker.exe", process_path=path)),
    )
    identity = _profile().identity
    run.llm_analysis_request.memory_profile = {
        "profile_id": identity.profile_id,
        "profile_version": identity.profile_version,
        "feature_schema_version": identity.feature_schema_version,
    }
    return run


def _command(run, profile):
    return memory_pattern_command_from_run(
        run,
        source_type=MemoryPatternSourceType.BATCH_ALERT,
        transport_ref=f"capacity-test:{run.run_id}",
        environment="dev-corpus-eval",
        data_class=MemoryPatternDataClass.OPERATIONAL,
        policy_fingerprint="a" * 64,
        profile=profile,
    )


def test_oversized_core_is_ineligible_without_rewriting_either_frozen_scope():
    profile = _profile()
    fingerprints = []
    for index, tail in enumerate(("one", "two"), start=1):
        run = _process_run("C:\\" + "d" * 600 + f"\\{tail}\\worker.exe", index)
        before = run.model_dump(mode="json")
        query = memory_query_from_analysis_request(run.llm_analysis_request, profile=profile)
        core = query.facets["behavior_component_core"]
        expected = "process_path:" + "d" * 600 + f"/{tail}/worker.exe"
        assert expected in core
        assert len(expected) > 512
        fingerprints.append(query.facets["behavior_fingerprint"])

        with pytest.raises(MemoryPatternIneligibleError, match="pattern_facet_value_out_of_bounds") as error:
            _command(run, profile)

        assert expected not in str(error.value)
        assert "behavior_component_core" in str(error.value)
        assert memory_query_from_analysis_request(run.llm_analysis_request, profile=profile).model_dump(mode="json") == query.model_dump(mode="json")
        assert run.model_dump(mode="json") == before
    assert fingerprints[0] != fingerprints[1]


def test_complete_512_character_core_remains_eligible():
    component = "process_path:" + "d" * (512 - len("process_path:/worker.exe")) + "/worker.exe"
    run = _process_run("C:\\" + component.removeprefix("process_path:").replace("/", "\\"))
    profile = _profile()
    query = memory_query_from_analysis_request(run.llm_analysis_request, profile=profile)

    command = _command(run, profile)

    assert len(component) == 512
    assert component in command.signature.facets["behavior_component_core"]
    assert command.signature.facets["behavior_fingerprint"] == query.facets["behavior_fingerprint"]
    assert command.profile_version == query.metadata["memory_profile_version"] == "9"


def test_long_non_pattern_facet_does_not_block_learning():
    run = _process_run("C:\\tools\\worker.exe")
    run.llm_analysis_request.classification.severity = "x" * 700
    profile = _profile()
    before = run.model_dump(mode="json")
    assert profile.project_run_facets(run)["severity"] == ["x" * 700]

    command = _command(run, profile)

    assert "severity" not in command.signature.facets
    assert command.signature.facets["behavior_component_core"]
    assert run.model_dump(mode="json") == before


def test_pattern_capacity_uses_existing_whitespace_normalization(monkeypatch):
    run = _process_run("C:\\tools\\worker.exe")
    profile = _profile()
    facets = profile.project_run_facets(run)
    facets["rule_name"] = ["first" + " " * 600 + "second"]
    monkeypatch.setattr(profile, "project_run_facets", lambda _: facets)

    command = _command(run, profile)

    assert command.signature.facets["rule_name"] == ["first second"]
    assert facets["rule_name"] == ["first" + " " * 600 + "second"]


def test_unrelated_pattern_schema_failure_still_propagates(monkeypatch):
    run = _process_run("C:\\tools\\worker.exe")
    profile = _profile()
    keys = (*profile_module._PINGAN_PATTERN_FACET_KEYS, "severity")
    assert len(keys) == 21
    facets = {key: ["bounded"] for key in keys}
    monkeypatch.setattr(profile_module, "_PINGAN_PATTERN_FACET_KEYS", keys)
    monkeypatch.setattr(profile, "project_run_facets", lambda _: facets)

    with pytest.raises(ValidationError, match="at most 20 facets"):
        _command(run, profile)
