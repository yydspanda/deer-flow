"""Vendor-neutral scenario taxonomy evaluation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from soc_agent.contracts import AlertSourceType, SocDomainName, SocDomainTriageRequest, SocSkillContext
from soc_agent.core import SocAnalysisService, SocDomainTriageService
from soc_agent.domain import SCENARIO_TAXONOMY_VERSION, scenario_taxonomy_keys


class ScenarioEvalFinding(BaseModel):
    """Compact scenario/domain finding snapshot for replay diff."""

    finding_id: str
    domain: SocDomainName
    title: str
    scenario_key: str | None = None
    scenario_name: str | None = None
    severity: str
    disposition: str
    confidence: float
    conclusion_summary: str | None = None
    evidence_gaps: list[str] = Field(default_factory=list)


class ScenarioEvalSampleResult(BaseModel):
    """One alert sample evaluated through deterministic scenario/domain triage."""

    sample_id: str
    path: str
    alert_id: str | None = None
    run_id: str | None = None
    domain: SocDomainName | None = None
    handler_id: str | None = None
    finding_count: int = Field(default=0, ge=0)
    scenario_finding_count: int = Field(default=0, ge=0)
    unmapped_vendor_scenario_count: int = Field(default=0, ge=0)
    scenario_keys: list[str] = Field(default_factory=list)
    passed: bool = False
    failure_reasons: list[str] = Field(default_factory=list)
    findings: list[ScenarioEvalFinding] = Field(default_factory=list)


class ScenarioEvalDiff(BaseModel):
    """Top-level replay diff between a baseline and the current scenario eval."""

    baseline_schema_version: str | None = None
    baseline_taxonomy_version: str | None = None
    changed: bool = False
    sample_count_delta: int = 0
    finding_count_delta: int = 0
    scenario_finding_count_delta: int = 0
    unmapped_vendor_scenario_count_delta: int = 0
    added_covered_scenario_keys: list[str] = Field(default_factory=list)
    removed_covered_scenario_keys: list[str] = Field(default_factory=list)
    newly_missing_scenario_keys: list[str] = Field(default_factory=list)
    no_longer_missing_scenario_keys: list[str] = Field(default_factory=list)


class ScenarioEvalReport(BaseModel):
    """Vendor-neutral deterministic scenario coverage report."""

    schema_version: str = "soc.scenario_eval_report.v1"
    scenario_taxonomy_version: str = SCENARIO_TAXONOMY_VERSION
    sample_count: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    scenario_finding_count: int = Field(default=0, ge=0)
    unmapped_vendor_scenario_count: int = Field(default=0, ge=0)
    passed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    domain_counts: dict[str, int] = Field(default_factory=dict)
    scenario_taxonomy_keys: list[str] = Field(default_factory=list)
    covered_scenario_keys: list[str] = Field(default_factory=list)
    missing_scenario_taxonomy_keys: list[str] = Field(default_factory=list)
    diff: ScenarioEvalDiff | None = None
    results: list[ScenarioEvalSampleResult] = Field(default_factory=list)


def run_scenario_eval(
    samples: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    baseline: ScenarioEvalReport | None = None,
) -> ScenarioEvalReport:
    """Run arbitrary alert samples through deterministic scenario/domain triage."""

    results = [_run_scenario_sample(path, payload) for path, payload in samples]
    taxonomy_keys = scenario_taxonomy_keys()
    covered_scenario_keys = sorted({key for result in results for key in result.scenario_keys if key != "vendor.unmapped"})
    domain_counts: dict[str, int] = {}
    for result in results:
        if result.domain is not None:
            domain_counts[result.domain.value] = domain_counts.get(result.domain.value, 0) + 1

    report = ScenarioEvalReport(
        sample_count=len(results),
        finding_count=sum(result.finding_count for result in results),
        scenario_finding_count=sum(result.scenario_finding_count for result in results),
        unmapped_vendor_scenario_count=sum(result.unmapped_vendor_scenario_count for result in results),
        passed_count=sum(result.passed for result in results),
        failed_count=sum(not result.passed for result in results),
        domain_counts=domain_counts,
        scenario_taxonomy_keys=taxonomy_keys,
        covered_scenario_keys=covered_scenario_keys,
        missing_scenario_taxonomy_keys=[key for key in taxonomy_keys if key not in covered_scenario_keys],
        results=results,
    )
    if baseline is not None:
        return report.model_copy(update={"diff": _scenario_eval_diff(baseline, report)})
    return report


def load_scenario_eval_report(path: str | Path) -> ScenarioEvalReport:
    """Load a prior scenario eval report for replay diff."""

    report_path = Path(path)
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read scenario eval baseline {report_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid scenario eval baseline JSON {report_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"scenario eval baseline must be a JSON object: {report_path}")
    return ScenarioEvalReport.model_validate(data)


def _run_scenario_sample(path: str, payload: Mapping[str, Any]) -> ScenarioEvalSampleResult:
    sample_id = Path(path).name
    try:
        run = SocAnalysisService().analyze(dict(payload))
        domain = _domain_for_source_type(run.normalization_report.source_type if run.normalization_report is not None else None)
        skill_context = run.llm_analysis_request.skill_context if run.llm_analysis_request is not None else SocSkillContext()
        result = SocDomainTriageService().triage(
            SocDomainTriageRequest(
                run=run,
                domain=domain,
                skill_context=skill_context,
                metadata={"eval_sample_id": sample_id, "source_path": path},
            )
        )
    except Exception as exc:  # noqa: BLE001 - eval should preserve batch progress
        return ScenarioEvalSampleResult(
            sample_id=sample_id,
            path=path,
            passed=False,
            failure_reasons=[str(exc)],
        )

    findings = [
        ScenarioEvalFinding(
            finding_id=finding.finding_id,
            domain=finding.domain,
            title=finding.title,
            scenario_key=finding.scenario_key,
            scenario_name=finding.scenario_name,
            severity=finding.severity.value,
            disposition=finding.disposition.value,
            confidence=finding.confidence,
            conclusion_summary=finding.current_conclusion.summary,
            evidence_gaps=finding.evidence_profile.gaps,
        )
        for finding in result.findings
    ]
    scenario_keys = sorted({finding.scenario_key for finding in result.findings if finding.scenario_key})
    failure_reasons: list[str] = []
    if not result.findings:
        failure_reasons.append("expected at least one domain finding")
    if result.metadata.get("writes_db") is not False:
        failure_reasons.append("domain triage result must not write DB")
    if result.metadata.get("executes_actions") is not False:
        failure_reasons.append("domain triage result must not execute actions")
    return ScenarioEvalSampleResult(
        sample_id=sample_id,
        path=path,
        alert_id=run.alert_id,
        run_id=run.run_id,
        domain=result.domain,
        handler_id=result.handler_id,
        finding_count=len(result.findings),
        scenario_finding_count=sum(1 for finding in result.findings if finding.scenario_key),
        unmapped_vendor_scenario_count=sum(1 for finding in result.findings if finding.scenario_key == "vendor.unmapped"),
        scenario_keys=scenario_keys,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        findings=findings,
    )


def _domain_for_source_type(source_type: AlertSourceType | None) -> SocDomainName:
    if source_type is AlertSourceType.EDR:
        return SocDomainName.EDR
    if source_type is AlertSourceType.HIDS:
        return SocDomainName.HIDS
    if source_type in {AlertSourceType.NDR, AlertSourceType.NIDS, AlertSourceType.THREAT_INTEL}:
        return SocDomainName.APT
    if source_type in {AlertSourceType.WAF, AlertSourceType.F5}:
        return SocDomainName.WAF_F5
    return SocDomainName.GENERIC


def _scenario_eval_diff(baseline: ScenarioEvalReport, current: ScenarioEvalReport) -> ScenarioEvalDiff:
    baseline_covered = set(baseline.covered_scenario_keys)
    current_covered = set(current.covered_scenario_keys)
    baseline_missing = set(baseline.missing_scenario_taxonomy_keys)
    current_missing = set(current.missing_scenario_taxonomy_keys)
    diff = ScenarioEvalDiff(
        baseline_schema_version=baseline.schema_version,
        baseline_taxonomy_version=baseline.scenario_taxonomy_version,
        sample_count_delta=current.sample_count - baseline.sample_count,
        finding_count_delta=current.finding_count - baseline.finding_count,
        scenario_finding_count_delta=current.scenario_finding_count - baseline.scenario_finding_count,
        unmapped_vendor_scenario_count_delta=current.unmapped_vendor_scenario_count - baseline.unmapped_vendor_scenario_count,
        added_covered_scenario_keys=sorted(current_covered - baseline_covered),
        removed_covered_scenario_keys=sorted(baseline_covered - current_covered),
        newly_missing_scenario_keys=sorted(current_missing - baseline_missing),
        no_longer_missing_scenario_keys=sorted(baseline_missing - current_missing),
    )
    return diff.model_copy(
        update={
            "changed": any(
                [
                    diff.sample_count_delta,
                    diff.finding_count_delta,
                    diff.scenario_finding_count_delta,
                    diff.unmapped_vendor_scenario_count_delta,
                    diff.added_covered_scenario_keys,
                    diff.removed_covered_scenario_keys,
                    diff.newly_missing_scenario_keys,
                    diff.no_longer_missing_scenario_keys,
                ]
            )
        }
    )


__all__ = [
    "ScenarioEvalDiff",
    "ScenarioEvalFinding",
    "ScenarioEvalReport",
    "ScenarioEvalSampleResult",
    "load_scenario_eval_report",
    "run_scenario_eval",
]
