"""Project canonical SOC lineage into the legacy ZEUS result envelope."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from soc_agent.contracts import (
    AnalysisRun,
    SocActionExecutionRecord,
    SocDecisionTransitionRecord,
    SocOperationalDisposition,
    Verdict,
)

_IGNORE_DISPOSITIONS = {
    SocOperationalDisposition.CLOSED_FALSE_POSITIVE,
    SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
    SocOperationalDisposition.SUPPRESSED,
    SocOperationalDisposition.IGNORED,
    SocOperationalDisposition.DUPLICATE,
}
_TRANSFER_DISPOSITIONS = {
    SocOperationalDisposition.CLOSED_TRUE_POSITIVE,
    SocOperationalDisposition.ESCALATED,
}


class PingAnLegacyResultMapper:
    """Read persisted Base -> Memory -> Tenant -> Effective lineage."""

    def project(
        self,
        run: AnalysisRun,
        *,
        decision_transitions: Sequence[SocDecisionTransitionRecord],
        action_executions: Sequence[SocActionExecutionRecord],
    ) -> dict[str, Any]:
        if run.decision is None:
            raise ValueError("legacy result projection requires a Runtime decision")
        transition = max(
            decision_transitions,
            key=lambda item: item.created_at,
            default=None,
        )
        base_verdict = run.decision.verdict
        effective_verdict = transition.after.verdict if transition is not None else base_verdict
        disposition = transition.effective_disposition if transition is not None else None
        alert_action = _legacy_action(effective_verdict, disposition)
        title = _alert_title(run)
        alert_type = _alert_type(run)
        rationale = run.decision.reason
        executions = [
            {
                "execution_id": item.execution_id,
                "route": item.route,
                "action": item.action,
                "status": item.status.value,
                "external_request_id": item.external_request_id,
            }
            for item in action_executions
        ]
        return {
            "alert_title": title,
            "alert_type": alert_type,
            "alert_action": alert_action,
            "alert_rationale": rationale,
            "disposal_action": (disposition.value if disposition is not None else ""),
            "disposal_rationale": run.decision.suggested_action,
            "warning_flag": 0 if alert_action == "忽略" else 1,
            "attack_detail": {
                "gen_answer": {
                    "attack_action": alert_action,
                    "attack_rationale": [rationale],
                },
                "trace_msg": [],
            },
            "evaluation": {
                "gen_answer": {
                    "evaluation_action": alert_action,
                    "evaluation_rationale": [rationale],
                },
                "trace_msg": None,
            },
            "disposal": {
                "gen_answer": {
                    "disposal_action": (disposition.value if disposition is not None else ""),
                    "disposal_rationale": run.decision.suggested_action,
                },
                "trace_msg": None,
            },
            "pa_code": "",
            "bu_name": "",
            "model_name": run.model_name,
            "soc_lineage": {
                "run_id": run.run_id,
                "base_verdict": base_verdict.value,
                "effective_verdict": effective_verdict.value,
                "decision_transition_id": (transition.transition_id if transition is not None else None),
                "effective_disposition": (disposition.value if disposition is not None else None),
                "action_executions": executions,
            },
        }

    def project_external_handled(
        self,
        *,
        alert_id: str,
        provider_status: str | None,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "alert_title": "",
            "alert_type": "",
            "alert_action": "已介入",
            "alert_rationale": reason,
            "disposal_action": "",
            "disposal_rationale": "",
            "warning_flag": 0,
            "attack_detail": {
                "gen_answer": {
                    "attack_action": "已介入",
                    "attack_rationale": [reason],
                },
                "trace_msg": [],
            },
            "evaluation": {
                "gen_answer": {
                    "evaluation_action": "已介入",
                    "evaluation_rationale": [reason],
                },
                "trace_msg": None,
            },
            "disposal": {
                "gen_answer": {
                    "disposal_action": "",
                    "disposal_rationale": "",
                },
                "trace_msg": None,
            },
            "pa_code": "",
            "bu_name": "",
            "model_name": "",
            "soc_lineage": {
                "run_id": None,
                "alert_id": alert_id,
                "external_lifecycle_status": provider_status,
                "skipped_before_analysis": True,
            },
        }

    def project_queue_expired(
        self,
        *,
        alert_id: str,
        elapsed_seconds: float,
        model_name: str | None,
    ) -> dict[str, Any]:
        elapsed_minutes = max(0.0, elapsed_seconds) / 60
        reason = f"排队超时：等待研判时间 {elapsed_minutes:.1f}分钟，超过配置的告警排队阈值。"
        return {
            "alert_title": "",
            "alert_type": "",
            "alert_action": "过期",
            "alert_rationale": reason,
            "disposal_action": "",
            "disposal_rationale": "",
            "warning_flag": 0,
            "attack_detail": {
                "gen_answer": {
                    "attack_action": "已跳过",
                    "attack_rationale": [],
                },
                "trace_msg": [],
            },
            "evaluation": {
                "gen_answer": {
                    "evaluation_action": "已跳过",
                    "evaluation_rationale": [reason],
                },
                "trace_msg": None,
            },
            "disposal": {
                "gen_answer": {
                    "disposal_action": "",
                    "disposal_rationale": "",
                },
                "trace_msg": None,
            },
            "pa_code": "",
            "bu_name": "",
            "model_name": model_name or "",
            "soc_lineage": {
                "run_id": None,
                "alert_id": alert_id,
                "skipped_before_analysis": True,
                "queue_deadline_expired": True,
                "elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
            },
        }


def _legacy_action(
    verdict: Verdict,
    disposition: SocOperationalDisposition | None,
) -> str:
    if disposition in _IGNORE_DISPOSITIONS:
        return "忽略"
    if disposition in _TRANSFER_DISPOSITIONS:
        return "转交"
    if verdict is Verdict.FALSE_POSITIVE:
        return "忽略"
    return "转交"


def _alert_title(run: AnalysisRun) -> str:
    if run.analysis is not None and run.analysis.summary.strip():
        return run.analysis.summary.strip()
    if run.normalized_alert is not None:
        rule_name = run.normalized_alert.classification.rule_name
        if rule_name:
            return rule_name
    return "SOC 告警研判"


def _alert_type(run: AnalysisRun) -> str:
    if run.normalized_alert is None:
        return ""
    return run.normalized_alert.source.source_type.value


__all__ = ["PingAnLegacyResultMapper"]
