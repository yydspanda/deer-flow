"""Strict loading and deterministic matching for SOC automation policies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from soc_agent.contracts import (
    AnalysisRun,
    SocAutomationPolicy,
    SocAutomationRule,
    SocDecisionSnapshot,
)
from soc_agent.utils.hashing import stable_hash


class SocAutomationPolicyError(ValueError):
    pass


def load_soc_automation_policy(path: str | Path) -> SocAutomationPolicy:
    policy_path = Path(path)
    try:
        source = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SocAutomationPolicyError(f"unable to read automation policy {policy_path}: {exc}") from exc
    try:
        document = json.loads(source) if policy_path.suffix.casefold() == ".json" else yaml.safe_load(source)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SocAutomationPolicyError(f"invalid automation policy {policy_path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise SocAutomationPolicyError("automation policy must contain one object")
    try:
        return SocAutomationPolicy.model_validate(document)
    except ValidationError as exc:
        raise SocAutomationPolicyError(f"invalid automation policy: {exc}") from exc


def automation_policy_hash(policy: SocAutomationPolicy) -> str:
    return stable_hash(policy.model_dump(mode="json"))


def select_automation_rule(
    policy: SocAutomationPolicy,
    run: AnalysisRun,
    decision: SocDecisionSnapshot,
) -> SocAutomationRule | None:
    request = run.llm_analysis_request
    analysis = run.analysis
    if request is None:
        return None
    scenario_keys = {item.scenario_key.casefold() for item in (analysis.scenario_assessments if analysis is not None else []) if item.scenario_key}
    for rule in sorted(
        (item for item in policy.rules if item.enabled),
        key=lambda item: (item.priority, item.rule_id),
    ):
        match = rule.match
        if match.verdicts and decision.verdict not in match.verdicts:
            continue
        if match.source_types and request.source.source_type not in match.source_types:
            continue
        if match.evidence_states and decision.evidence_state not in match.evidence_states:
            continue
        if match.model_names and run.model_name not in match.model_names:
            continue
        if match.prompt_versions and run.prompt_version not in match.prompt_versions:
            continue
        if match.decision_policy_versions and decision.policy_version not in match.decision_policy_versions:
            continue
        if match.minimum_confidence is not None and decision.confidence < match.minimum_confidence:
            continue
        if match.needs_review is not None and decision.needs_review is not match.needs_review:
            continue
        expected_scenarios = {value.casefold() for value in match.scenario_keys}
        if expected_scenarios and not (scenario_keys & expected_scenarios):
            continue
        return rule
    return None


__all__ = [
    "SocAutomationPolicyError",
    "automation_policy_hash",
    "load_soc_automation_policy",
    "select_automation_rule",
]
