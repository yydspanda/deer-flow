#!/usr/bin/env python3
"""Mandatory offline checks for historical SOC records before a PingAn release."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

FIXTURE_PATH = "backend/tests/fixtures/soc_memory/profile7_v5_release_20260918.json"
REQUIRED_TEST_FILES = (
    "backend/tests/test_soc_release_memory_compatibility.py",
    "backend/tests/test_soc_pingan_memory_profile_restore.py",
    "backend/tests/test_soc_corpus_pattern_execution.py",
    "backend/tests/test_soc_memory_experiment_retrieval.py",
    "backend/tests/test_soc_memory_reference_retrieval.py",
    "backend/tests/test_soc_memory_reference_compatibility_safety.py",
    "backend/tests/test_soc_pingan_memory_service_direction.py",
)
# Keep these behavioral checks mandatory even if a file still contains other tests.
REQUIRED_TEST_NAMES: dict[str, tuple[str, ...]] = {
    "backend/tests/test_soc_release_memory_compatibility.py": (
        "test_frozen_history_survives_upgrade",
        "test_frozen_reviewed_memory_still_reuses_in_validation",
        "test_frozen_reviewed_memory_rejects_out_of_scope",
        "test_frozen_baseline_integrity",
    ),
    "backend/tests/test_soc_pingan_memory_profile_restore.py": (
        "test_saved_profile_identity_overrides_review_mode",
        "test_unmarked_historical_run_keeps_legacy_review_mode_inference",
        "test_invalid_saved_identity_is_rejected_without_mode_fallback",
    ),
    "backend/tests/test_soc_corpus_pattern_execution.py": (
        "test_saved_observation_remains_visible_in_execution_audit_and_list",
    ),
    "backend/tests/test_soc_memory_experiment_retrieval.py": (
        "test_round_allowlist_applies_to_both_context_and_direct_lookup_without_changing_authority",
        "test_empty_allowlist_survives_transactional_governance_clone",
    ),
    "backend/tests/test_soc_memory_reference_retrieval.py": (
        "test_optional_service_difference_retains_reviewed_reference_without_directive",
        "test_known_profile_change_can_only_recall_rule_context_as_reference",
        "test_current_response_projection_recalls_old_reference_with_explicit_port_comparison",
        "test_match_preview_uses_same_profile_conflicts_as_runtime",
    ),
    "backend/tests/test_soc_memory_reference_compatibility_safety.py": (
        "test_optional_port_compatibility_is_context_only_and_preserves_frozen_record",
        "test_no_registered_tenant_profile_cannot_authorize_cross_version_compatibility",
        "test_unknown_query_identity_is_not_a_known_compatibility_family",
        "test_unknown_saved_profile_identity_is_not_reinterpreted",
        "test_cross_tenant_query_never_sees_reference_compatibility_record",
        "test_compatibility_never_drops_reviewed_conditions",
        "test_non_port_semantic_conflicts_still_reject_reference",
        "test_decision_bearing_records_never_enter_reference_compatibility",
        "test_match_test_uses_same_tenant_policy_as_runtime_retrieval",
    ),
    "backend/tests/test_soc_pingan_memory_service_direction.py": (
        "test_service_and_strong_anchor_follow_the_same_explicit_direction",
        "test_unknown_direction_does_not_guess_a_service",
        "test_invalid_service_port_does_not_fall_back_to_other_endpoint_or_http",
        "test_unknown_transport_does_not_create_service_anchor",
        "test_bound_observation_uses_only_its_own_direction_transport_and_ports",
        "test_aggregate_with_distinct_connections_needs_an_explicit_observation_binding",
        "test_reversed_request_response_observations_do_not_invent_two_connections",
        "test_current_identity_and_run_projection_use_directional_features",
        "test_current_scope_expands_only_its_verified_directional_fingerprint",
        "test_historical_profile_features_remain_byte_equivalent",
        "test_fresh_requests_select_new_identity_only_when_canonical_features_change",
        "test_new_projection_gap_is_not_hidden_when_duplicate_service_anchors_collapse",
        "test_saved_identity_never_runs_fresh_feature_selection",
        "test_invalid_saved_identity_is_not_replaced_by_an_unchanged_projection",
        "test_unchanged_features_keep_reviewed_v9_directive_on_its_exact_path",
    ),
}
TIMEOUT_SECONDS = 600


class CompatibilityGateError(ValueError):
    """Release cannot proceed without a complete successful check."""

    def __init__(self, reason: str, *, log_path: Path | None = None):
        self.log_path = log_path
        message = f"SOC compatibility gate failed: {reason}"
        if log_path is not None:
            message += f"; pytest log: {log_path}"
        super().__init__(message)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _test_environment(workspace: Path) -> dict[str, str]:
    excluded_prefixes = ("PYTEST", "SOC_", "PINGAN_", "DEER_FLOW_")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(excluded_prefixes)
        and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    home = workspace / "runtime"
    home.mkdir()
    config = workspace / "config.yaml"
    config.write_text("models: []\n", encoding="utf-8")
    environment.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "SOC_DATABASE_URL": "sqlite:///:memory:",
            "SOC_NORMALIZATION_ASSIST_MODE": "off",
            "DEER_FLOW_HOME": str(home),
            "DEER_FLOW_CONFIG_PATH": str(config),
        }
    )
    return environment


def _junit_results(path: Path) -> tuple[int, dict[str, list[str]]]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise CompatibilityGateError("missing or malformed JUnit report") from exc
    if root.tag not in {"testsuites", "testsuite"}:
        raise CompatibilityGateError("unexpected JUnit report root")
    cases = list(root.iter("testcase"))
    if not cases:
        raise CompatibilityGateError("zero test cases executed")
    if any(
        next(root.iter(tag), None) is not None
        for tag in ("failure", "error", "skipped")
    ):
        raise CompatibilityGateError(
            "nonpassing test results (failure, error, skip or xfail)"
        )
    try:
        suites = [
            suite for suite in root.iter("testsuite") if suite.find("testsuite") is None
        ]
        if not suites:
            raise ValueError("missing suites")
        if any(
            int(suite.get(key, "0")) != 0
            for suite in suites
            for key in ("errors", "failures", "skipped")
        ):
            raise CompatibilityGateError("nonpassing JUnit suite totals")
        if sum(int(suite.attrib["tests"]) for suite in suites) != len(cases):
            raise ValueError("inconsistent test count")
    except (KeyError, ValueError) as exc:
        raise CompatibilityGateError("invalid JUnit test count") from exc

    tests_by_file: dict[str, list[str]] = {
        relative: [] for relative in REQUIRED_TEST_FILES
    }
    modules = {Path(relative).stem: relative for relative in REQUIRED_TEST_FILES}
    for case in cases:
        components = case.get("classname", "").split(".")
        matches = [
            relative for module, relative in modules.items() if module in components
        ]
        name = case.get("name", "")
        if len(matches) != 1 or not name:
            raise CompatibilityGateError("unidentified test case in JUnit report")
        tests_by_file[matches[0]].append(name)
    for relative, names in tests_by_file.items():
        if not names:
            raise CompatibilityGateError(f"missing required test execution: {relative}")
        actual = {name.split("[", 1)[0] for name in names}
        for required in REQUIRED_TEST_NAMES.get(relative, ()):
            if required not in actual:
                raise CompatibilityGateError(
                    f"missing required test execution: {relative}::{required}"
                )
    return len(cases), tests_by_file


def run_compatibility_gate(
    root: Path, *, source_commit: str, source_fingerprint: str
) -> dict[str, Any]:
    """Execute the fixed test set; only verified JUnit results produce a receipt.

    The transfer builder owns checking that source identity is unchanged before
    and after this function. Neither a caller-supplied result nor a skip option is
    accepted. All business state used by tests is synthetic and temporary.
    """
    root = root.resolve()
    python = root / "backend/.venv/bin/python"
    for relative in ("backend/.venv/bin/python", *REQUIRED_TEST_FILES, FIXTURE_PATH):
        if not (root / relative).is_file():
            raise CompatibilityGateError(f"missing required file: {relative}")
    fixture = root / FIXTURE_PATH
    try:
        metadata = json.loads(fixture.read_text(encoding="utf-8"))
        fixture_identity = {
            key: metadata[key]
            for key in ("fixture_id", "source_commit", "payload_sha256")
        }
        if any(
            not isinstance(value, str) or not value
            for value in fixture_identity.values()
        ):
            raise ValueError("empty fixture identity")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CompatibilityGateError(
            "missing or invalid frozen fixture identity"
        ) from exc

    workspace = Path(tempfile.mkdtemp(prefix="soc-pingan-compatibility-"))
    junit = workspace / "results.xml"
    log = workspace / "pytest.log"
    command = [
        str(python),
        "-m",
        "pytest",
        "-q",
        "-o",
        "addopts=",
        "-o",
        "xfail_strict=true",
        "-o",
        "cache_dir=" + str(workspace / "pytest-cache"),
        "--basetemp=" + str(workspace / "pytest-tmp"),
        "--junitxml=" + str(junit),
        *(relative.removeprefix("backend/") for relative in REQUIRED_TEST_FILES),
    ]
    fixture_hash = _sha256(fixture)
    started = time.monotonic()
    print(
        "Checking historical SOC records and approved-memory reuse (offline)...",
        file=sys.stderr,
        flush=True,
    )
    try:
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(
                command,
                cwd=root / "backend",
                env=_test_environment(workspace),
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        if result.returncode:
            raise CompatibilityGateError(
                f"pytest exited with status {result.returncode}"
            )
        passed, tests_by_file = _junit_results(junit)
        if _sha256(fixture) != fixture_hash:
            raise CompatibilityGateError("frozen fixture changed during checks")
    except subprocess.TimeoutExpired as exc:
        raise CompatibilityGateError(
            f"pytest timed out after {TIMEOUT_SECONDS} seconds", log_path=log
        ) from exc
    except OSError as exc:
        raise CompatibilityGateError(
            "pytest could not start or finish reading evidence", log_path=log
        ) from exc
    except CompatibilityGateError as exc:
        raise CompatibilityGateError(
            str(exc).removeprefix("SOC compatibility gate failed: "), log_path=log
        ) from exc

    duration = round(time.monotonic() - started, 3)
    print(
        f"SOC compatibility gate passed: {passed} tests in {duration}s.",
        file=sys.stderr,
        flush=True,
    )
    return {
        "status": "passed",
        "source_commit": source_commit,
        "source_fingerprint": source_fingerprint,
        "fixture": {"path": FIXTURE_PATH, "sha256": fixture_hash, **fixture_identity},
        "passed": passed,
        "tests_by_file": tests_by_file,
        "duration_seconds": duration,
        "evidence": {
            "junit_path": str(junit),
            "junit_sha256": _sha256(junit),
            "log_path": str(log),
            "log_sha256": _sha256(log),
        },
    }
