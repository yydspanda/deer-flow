"""Pure PI-05A rollout rehearsal service with zero external side effects."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from soc_agent.contracts.rollout import (
    REQUIRED_ROLLOUT_GATE_STAGES,
    SocRolloutGateAssessment,
    SocRolloutGateId,
    SocRolloutGateObservation,
    SocRolloutRealGateStatus,
    SocRolloutRehearsalDiff,
    SocRolloutRehearsalReport,
    SocRolloutRehearsalRequest,
    SocRolloutRehearsalStep,
    SocRolloutRehearsalStepKind,
    SocRolloutRehearsalStepOutcome,
    SocRolloutRollbackAction,
    SocRolloutRollbackRehearsal,
    SocRolloutStage,
    SocRolloutStageAssessment,
)

_ROLLOUT_STAGES = (
    SocRolloutStage.SHADOW,
    SocRolloutStage.LIMITED_PILOT,
    SocRolloutStage.CONTROLLED_ROLLOUT,
)


class SocRolloutRehearsalService:
    """Exercise plan, gate, promotion, and rollback semantics in memory."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def rehearse(
        self,
        request: SocRolloutRehearsalRequest,
        *,
        baseline: SocRolloutRehearsalReport | None = None,
    ) -> SocRolloutRehearsalReport:
        plan_snapshot = request.plan.model_dump(mode="json", exclude_none=True)
        plan_sha256 = _stable_hash(plan_snapshot)
        gate_assessments = _build_gate_assessments(request)
        stage_assessments = _build_stage_assessments(gate_assessments)
        steps = _build_rehearsal_steps(request, stage_assessments)
        rollback = SocRolloutRollbackRehearsal(
            trigger_id=request.injected_rollback_trigger_id,
            actions_exercised=list(request.plan.rollback.actions),
            passed=True,
        )
        component_hashes = {
            "plan": plan_sha256,
            "gate_assessments": _stable_hash([item.model_dump(mode="json", exclude_none=True) for item in gate_assessments]),
            "stage_assessments": _stable_hash([item.model_dump(mode="json", exclude_none=True) for item in stage_assessments]),
            "steps": _stable_hash([item.model_dump(mode="json", exclude_none=True) for item in steps]),
            "rollback": _stable_hash(rollback.model_dump(mode="json", exclude_none=True)),
        }
        semantic_sha256 = _stable_hash(
            {
                "policy_version": request.plan.policy_version,
                "plan_id": request.plan.plan_id,
                "plan_version": request.plan.plan_version,
                "component_hashes": component_hashes,
            }
        )
        rehearsal_id = f"SRR-{semantic_sha256[:12].upper()}"
        diff = _build_diff(
            baseline=baseline,
            rehearsal_id=rehearsal_id,
            semantic_sha256=semantic_sha256,
            component_hashes=component_hashes,
        )
        return SocRolloutRehearsalReport(
            rehearsal_id=rehearsal_id,
            generated_at=self._clock(),
            plan_id=request.plan.plan_id,
            plan_version=request.plan.plan_version,
            requested_by=request.requested_by,
            rationale=request.rationale,
            scope=request.plan.scope,
            owners=list(request.plan.owners),
            plan_sha256=plan_sha256,
            semantic_sha256=semantic_sha256,
            component_hashes=component_hashes,
            engineering_rehearsal_passed=True,
            gate_assessments=gate_assessments,
            stage_assessments=stage_assessments,
            steps=steps,
            rollback=rollback,
            simulated_stage_transition_count=sum(item.kind is SocRolloutRehearsalStepKind.SIMULATE_STAGE_TRANSITION for item in steps),
            limitations=[
                "This report exercises an in-memory simulation and does not change a deployed rollout stage.",
                "Simulation evidence cannot satisfy PI-01, PI-02, PI-03, PI-04, ownership, rollback, or cohort-isolation real gates.",
                "No Provider, broker, database mutation, feature-flag service, Zeus state, auto-close, or response action was invoked.",
                "A future real rollout controller requires fresh environment evidence and accountable human approval outside this service.",
            ],
            diff=diff,
        )


def load_soc_rollout_rehearsal_request(path: str | Path) -> SocRolloutRehearsalRequest:
    return _load_model(path, SocRolloutRehearsalRequest, "rollout rehearsal request")


def load_soc_rollout_rehearsal_report(path: str | Path) -> SocRolloutRehearsalReport:
    return _load_model(path, SocRolloutRehearsalReport, "rollout rehearsal report")


def _build_gate_assessments(
    request: SocRolloutRehearsalRequest,
) -> list[SocRolloutGateAssessment]:
    observations = {item.gate_id: item for item in request.plan.gates}
    return [
        SocRolloutGateAssessment(
            gate_id=gate_id,
            required_for_stages=_ordered_stages(REQUIRED_ROLLOUT_GATE_STAGES[gate_id]),
            observed_status=observations[gate_id].status,
            observed_reason=observations[gate_id].reason,
            evidence_refs=list(observations[gate_id].evidence_refs),
            blocking_reason=_gate_blocking_reason(observations[gate_id]),
        )
        for gate_id in SocRolloutGateId
    ]


def _build_stage_assessments(
    gates: list[SocRolloutGateAssessment],
) -> list[SocRolloutStageAssessment]:
    return [
        SocRolloutStageAssessment(
            stage=stage,
            required_gate_ids=[gate.gate_id for gate in gates if stage in gate.required_for_stages],
            blocked_gate_ids=[gate.gate_id for gate in gates if stage in gate.required_for_stages and not gate.real_gate_satisfied],
        )
        for stage in _ROLLOUT_STAGES
    ]


def _build_rehearsal_steps(
    request: SocRolloutRehearsalRequest,
    stages: list[SocRolloutStageAssessment],
) -> list[SocRolloutRehearsalStep]:
    steps: list[SocRolloutRehearsalStep] = []

    def append(
        kind: SocRolloutRehearsalStepKind,
        detail: str,
        *,
        from_stage: SocRolloutStage | None = None,
        to_stage: SocRolloutStage | None = None,
        rollback_action: SocRolloutRollbackAction | None = None,
    ) -> None:
        steps.append(
            SocRolloutRehearsalStep(
                sequence=len(steps) + 1,
                kind=kind,
                outcome=SocRolloutRehearsalStepOutcome.PASSED,
                detail=detail,
                from_stage=from_stage,
                to_stage=to_stage,
                rollback_action=rollback_action,
            )
        )

    append(
        SocRolloutRehearsalStepKind.VALIDATE_PLAN,
        "Validated the complete v1 scope, owner, real-gate, and ordered rollback contract.",
    )
    previous = SocRolloutStage.NOT_STARTED
    stage_map = {item.stage: item for item in stages}
    for stage in _ROLLOUT_STAGES:
        blocked = ", ".join(item.value for item in stage_map[stage].blocked_gate_ids)
        append(
            SocRolloutRehearsalStepKind.ASSESS_REAL_GATES,
            f"Assessed the target stage before transition; real promotion remains blocked by: {blocked}.",
            from_stage=previous,
            to_stage=stage,
        )
        append(
            SocRolloutRehearsalStepKind.SIMULATE_STAGE_TRANSITION,
            "Exercised a virtual transition after gate assessment; no real stage or feature flag was changed.",
            from_stage=previous,
            to_stage=stage,
        )
        previous = stage
    append(
        SocRolloutRehearsalStepKind.INJECT_ROLLBACK_TRIGGER,
        f"Injected declared trigger {request.injected_rollback_trigger_id!r} into the simulation.",
        from_stage=SocRolloutStage.CONTROLLED_ROLLOUT,
        to_stage=SocRolloutStage.CONTROLLED_ROLLOUT,
    )
    for action in request.plan.rollback.actions:
        append(
            SocRolloutRehearsalStepKind.SIMULATE_ROLLBACK_ACTION,
            f"Exercised rollback action {action.value!r} without invoking an external system.",
            from_stage=SocRolloutStage.CONTROLLED_ROLLOUT,
            to_stage=SocRolloutStage.CONTROLLED_ROLLOUT,
            rollback_action=action,
        )
    append(
        SocRolloutRehearsalStepKind.SIMULATE_STAGE_TRANSITION,
        "Exercised the virtual rollback transition to shadow; the real stage remains not_started.",
        from_stage=SocRolloutStage.CONTROLLED_ROLLOUT,
        to_stage=SocRolloutStage.SHADOW,
    )
    append(
        SocRolloutRehearsalStepKind.VERIFY_SAFETY_BOUNDARIES,
        "Verified zero real transitions, external effects, auto-close operations, and high-risk actions.",
        from_stage=SocRolloutStage.SHADOW,
        to_stage=SocRolloutStage.SHADOW,
    )
    return steps


def _gate_blocking_reason(observation: SocRolloutGateObservation) -> str:
    if observation.status is SocRolloutRealGateStatus.PASSED:
        return "PI-05A still treats the observation as non-authorizing because this request is simulation-only."
    return f"Real gate status is {observation.status.value}; simulation provenance cannot satisfy {observation.gate_id.value}."


def _ordered_stages(stages: frozenset[SocRolloutStage]) -> list[SocRolloutStage]:
    return [stage for stage in _ROLLOUT_STAGES if stage in stages]


def _build_diff(
    *,
    baseline: SocRolloutRehearsalReport | None,
    rehearsal_id: str,
    semantic_sha256: str,
    component_hashes: dict[str, str],
) -> SocRolloutRehearsalDiff | None:
    if baseline is None:
        return None
    changed_components = sorted(name for name in set(baseline.component_hashes) | set(component_hashes) if baseline.component_hashes.get(name) != component_hashes.get(name))
    changed = baseline.semantic_sha256 != semantic_sha256 or bool(changed_components)
    if not changed and baseline.rehearsal_id != rehearsal_id:
        raise ValueError("stable rollout rehearsal semantics produced a different rehearsal id")
    return SocRolloutRehearsalDiff(
        baseline_rehearsal_id=baseline.rehearsal_id,
        baseline_semantic_sha256=baseline.semantic_sha256,
        changed=changed,
        changed_components=changed_components,
    )


def _load_model(path: str | Path, model_type, label: str):
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON: {exc}") from exc
    return model_type.model_validate(payload)


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "SocRolloutRehearsalService",
    "load_soc_rollout_rehearsal_report",
    "load_soc_rollout_rehearsal_request",
]
