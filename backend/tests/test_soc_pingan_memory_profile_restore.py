"""Historical readers honor the exact Memory contract frozen by the writer."""

import pytest
from test_soc_pingan_memory_profile import _run

from soc_agent.contracts import NormalizationAssistResult
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory.profiles import SocMemoryProfileRegistry

_IDENTITIES = {
    "7": {"profile_id": "pingan.soc", "profile_version": "7", "feature_schema_version": "pingan.soc.memory_features.v5"},
    "8": {"profile_id": "pingan.soc", "profile_version": "8", "feature_schema_version": "pingan.soc.memory_features.v6"},
    "9": {"profile_id": "pingan.soc", "profile_version": "9", "feature_schema_version": "pingan.soc.memory_features.v7"},
}
_REVIEW_CASES = [
    pytest.param(None, None, id="off-report-absent"),
    pytest.param("shadow", "shadow", id="shadow"),
    pytest.param("apply", "applied", id="apply"),
    pytest.param("shadow", "failed", id="shadow-provider-failed"),
    pytest.param("apply", "failed", id="apply-provider-failed"),
]


def _saved_run(identity, review_mode, review_status):
    run = _run(1)
    run.llm_analysis_request.memory_profile = dict(identity)
    if review_mode is not None:
        run.normalization_assistance = NormalizationAssistResult(
            mode=review_mode,
            status=review_status,
            request_hash="synthetic-review-request",
            model_name="synthetic-no-provider-call",
            prompt_version="synthetic-review-v1",
        )
    return run


@pytest.mark.parametrize("version", ["7", "8", "9"])
@pytest.mark.parametrize(("review_mode", "review_status"), _REVIEW_CASES)
def test_saved_profile_identity_overrides_review_mode(version, review_mode, review_status):
    """Per-run review switches must not hide observations saved by that run."""
    run = _saved_run(_IDENTITIES[version], review_mode, review_status)
    before = run.model_dump_json()
    # Current deployment settings may differ from both saved identity and review mode.
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=version == "7")])

    restored = PingAnSocMemoryProfile.for_run(run)

    assert restored.identity.profile_version == version
    assert restored.identity.feature_schema_version == _IDENTITIES[version]["feature_schema_version"]
    assert restored.identity == registry.resolve_run(run).identity
    assert restored.semantic_features is (version != "7")
    if version != "7":
        assert restored.stable_semantics is (version == "9")
    assert run.model_dump_json() == before


@pytest.mark.parametrize(("review_mode", "review_status"), _REVIEW_CASES)
def test_unmarked_historical_run_keeps_legacy_review_mode_inference(review_mode, review_status):
    run = _saved_run({}, review_mode, review_status)
    expected_version = "8" if review_mode == "apply" else "7"
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=True)])

    restored = PingAnSocMemoryProfile.for_run(run)

    assert restored.identity.profile_version == expected_version
    assert restored.identity.feature_schema_version == _IDENTITIES[expected_version]["feature_schema_version"]
    assert restored.identity == registry.resolve_run(run).identity


@pytest.mark.parametrize(
    "identity",
    [
        {**_IDENTITIES["9"], "profile_id": "foreign.soc"},
        {**_IDENTITIES["9"], "profile_version": "999"},
        {**_IDENTITIES["9"], "feature_schema_version": "pingan.soc.memory_features.v5"},
        {key: value for key, value in _IDENTITIES["9"].items() if key != "profile_id"},
        {key: value for key, value in _IDENTITIES["9"].items() if key != "profile_version"},
        {key: value for key, value in _IDENTITIES["9"].items() if key != "feature_schema_version"},
    ],
    ids=["foreign-profile", "unknown-version", "mismatched-schema", "missing-profile", "missing-version", "missing-schema"],
)
def test_invalid_saved_identity_is_rejected_without_mode_fallback(identity):
    run = _saved_run(identity, "apply", "applied")
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=True)])

    with pytest.raises(ValueError, match="saved PingAn Memory"):
        PingAnSocMemoryProfile.for_run(run)
    with pytest.raises(ValueError, match="saved PingAn Memory"):
        registry.resolve_run(run)
