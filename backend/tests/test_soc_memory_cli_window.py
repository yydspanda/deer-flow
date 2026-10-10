"""CLI accumulation defaults inherit frozen Runtime policy without a database."""

import argparse
from datetime import timedelta

import pytest
from test_soc_memory_patterns import _run as generic_run
from test_soc_pingan_memory_profile import _observe
from test_soc_pingan_memory_profile import _run as pingan_run

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.cli import _add_memory_pattern_args, _memory_pattern_service_from_args
from soc_agent.memory import InMemoryMemoryPatternRepository
from soc_agent.memory.projection_audit import bind_memory_profile


def _args(*overrides):
    parser = argparse.ArgumentParser()
    _add_memory_pattern_args(parser)
    return parser.parse_args(
        [
            "--memory-pattern-data-class",
            "operational",
            "--memory-pattern-environment",
            "prd",
            *overrides,
        ]
    )


def _observe_from_cli(run, args):
    repository = InMemoryMemoryPatternRepository()
    service, environment, data_class = _memory_pattern_service_from_args(args, repository)
    assert environment == "prd"
    assert data_class.value == "operational"
    request = run.llm_analysis_request
    profile = build_soc_memory_profile_registry().resolve_request(request)
    run.llm_analysis_request = bind_memory_profile(request, profile)
    return _observe(service, run, "cli-window:1").observation


def test_unset_cli_policy_fields_are_unspecified():
    args = _args()
    assert args.memory_pattern_window_seconds is None
    assert args.memory_pattern_minimum_support is None
    assert args.memory_pattern_minimum_distinct_sources is None


@pytest.mark.parametrize(("run_factory", "days"), [(pingan_run, 180), (generic_run, 1)])
def test_cli_defaults_inherit_the_fresh_run_profile(run_factory, days, monkeypatch):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")
    observation = _observe_from_cli(run_factory(1), _args())
    assert observation.aggregation_policy.window_seconds == days * 86_400
    assert observation.window_end - observation.window_start == timedelta(days=days)


def test_cli_explicit_window_still_overrides_the_frozen_run(monkeypatch):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")
    observation = _observe_from_cli(pingan_run(1), _args("--memory-pattern-window-seconds", str(7 * 86_400)))
    assert observation.aggregation_policy.window_seconds == 7 * 86_400
    assert observation.window_end - observation.window_start == timedelta(days=7)
    assert observation.aggregation_policy.minimum_support == 5
    assert observation.aggregation_policy.minimum_distinct_sources == 5


@pytest.mark.parametrize(
    ("option", "field", "value"),
    [
        ("--memory-pattern-minimum-support", "minimum_support", 7),
        ("--memory-pattern-minimum-distinct-sources", "minimum_distinct_sources", 3),
    ],
)
def test_cli_partial_override_builds_an_explicit_policy_from_generic_defaults(option, field, value, monkeypatch):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")
    observation = _observe_from_cli(pingan_run(1), _args(option, str(value)))
    assert observation.aggregation_policy.window_seconds == 86_400
    assert getattr(observation.aggregation_policy, field) == value
