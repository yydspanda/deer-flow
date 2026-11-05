"""Two AI calls, compact NAS evidence, then one deduplicated ZEUS submission."""

import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from . import surge_ai_review as base
from . import surge_sampling
from . import topic_ai
from .surge_history import MaterialsPendingError
from .surge_processor import (
    ATTEMPT_KEYS,
    DurableSurgeError,
    SurgeProcessor,
    _hash,
    _identity,
    _read,
    _read_bytes,
    _write,
)
from .topic_delivery import TopicDelivery

SCHEMA = "sec_know.topic_filter_record.v1"
SUMMARY_FIELDS = (
    "issue",
    "recommendation",
    "certainty",
    "review_notes",
    "topic_review",
)
COVERAGE_FIELDS = (
    "current_alert_count",
    "current_log_count",
    "historical_available_unique_alerts",
    "history_selected_unique_alerts",
    "history_log_count",
    "cutoff",
    "cutoff_basis",
    "current_first_alert_at",
    "current_last_alert_at",
    "history_first_alert_at",
    "history_last_alert_at",
    "history_selection_basis",
    "database_scan_complete",
)
TRANSPORT_FIELDS = (
    "requested_model",
    "returned_model",
    "finish_reason",
    "sdk_request_calls",
    "elapsed_ms",
    "usage",
    "error_code",
    "response_received",
    "request_config_sha256",
    "endpoint_sha256",
    "sdk_max_retries",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _compact(value):
    if len(base.encoded(value)) > 64 * 1024:
        raise ValueError("compact_record_over_64k")
    return value


def _evidence(reply, selection):
    """Only five citations and short values of actual candidate fields survive."""
    samples = {
        s["alert_id"]: s
        for s in selection["current_samples"] + selection["history_samples"]
    }
    definition = reply.get("filter") or {}
    fields = list(
        dict.fromkeys(
            v["name"] for k, v in definition.items() if k not in {"topic", "expression"}
        )
    )[:5]
    result = []
    for identity in reply.get("sample_alert_ids", [])[:5]:
        sample = samples[identity]
        excerpts = {}
        for field in fields:
            for log in sample["raw_logs"]:
                value = surge_sampling.field_value(log, field)
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    text = str(value)
                    excerpts[field] = {"text": text[:160], "truncated": len(text) > 160}
                    break
        result.append(
            {
                "alert_id": identity,
                "source_role": sample.get("source_role"),
                "fields": excerpts,
            }
        )
    return result


class TopicProcessor(SurgeProcessor):
    def __init__(
        self,
        directory,
        *,
        load_case,
        load_rules,
        generate,
        model,
        budget,
        submit,
        operator_context="",
        delivery_directory=None,
        delivery_scope=None,
        ai_contract=None,
    ):
        super().__init__(
            directory,
            load_case=load_case,
            load_rules=load_rules,
            generate=generate,
            model=model,
            budget=budget,
            operator_context=operator_context,
        )
        self.delivery = TopicDelivery(
            delivery_directory or self.directory / "deliveries", submit
        )
        self.delivery_scope = delivery_scope
        self.ai = topic_ai
        if ai_contract == "topic-condition-tree-v1":
            from . import topic_tree_ai

            self.ai = topic_tree_ai
        elif ai_contract is not None:
            raise ValueError("unsupported_topic_ai_contract")

    def _outcome(self, folder, job_id, status, **kw):
        return {
            **super()._outcome(folder, job_id, status, **kw),
            "optimization_scope": topic_ai.SCOPE,
            "activation_state": "managed_by_zeus_not_verified",
        }

    def _process(self, folder, signal, scope, job_id):
        if (folder / "result.json").exists():
            record = _read(folder / "result.json")
            result = {
                **record["outcome"],
                "record_id": record["record_id"],
                "analysis_reused": True,
                "model_calls_this_attempt": 0,
            }
            self.verify_durable_result(signal, result)
            return result
        input_record = {
            "schema_version": SCHEMA,
            "job_id": job_id,
            "scope": scope,
            "signal_sha256": _hash(signal),
            "model": self.model,
            "prompt_version": self.ai.PROMPT_VERSION,
            "budget": self.budget.audit(),
            "operator_context_sha256": _hash(self.operator_context),
        }
        _write(folder / "input.json", _compact(input_record))
        # A reviewed compact candidate is sufficient to resume delivery; raw logs
        # are intentionally not persisted. The delivery ledger owns POST ambiguity.
        if (folder / "recommendation.json").exists():
            candidate = _read(folder / "recommendation.json")
            return self._deliver(folder, signal, job_id, candidate, calls=0)
        if any(folder.glob("*.started.json")):
            return self._finish(
                folder,
                signal,
                job_id,
                "blocked",
                calls=0,
                reason="analysis_interrupted_no_automatic_replay",
            )
        try:
            case = self.load_case(copy.deepcopy(signal))
        except MaterialsPendingError:
            return self._outcome(folder, job_id, "materials_pending")
        self._validate_case(case, scope)
        execute = case.get("execute_type", {})
        values = execute.get("values")
        if (
            execute.get("status") != "resolved"
            or not isinstance(values, list)
            or len(values) != 1
            or type(values[0]) is not int
            or values[0] not in range(4)
        ):
            _write(
                folder / "execute-type.json",
                {
                    "status": "needs_confirmation",
                    "reason": "current_execute_type_missing_invalid_or_mixed",
                    "basis": "current_alerts.alert.executeType",
                },
            )
            return self._finish(
                folder,
                signal,
                job_id,
                "manual_review",
                calls=0,
                reason="execute_type_needs_confirmation",
            )
        _write(
            folder / "materials-summary.json",
            _compact(
                {
                    "current_count": len(case["current_samples"]),
                    "history_count": len(case["history_samples"]),
                    "coverage": {
                        k: case["coverage"][k]
                        for k in COVERAGE_FIELDS
                        if k in case["coverage"]
                    },
                    "material_sha256": _hash(
                        {
                            k: case[k]
                            for k in ("rule", "current_samples", "history_samples")
                        }
                    ),
                    "execute_type": execute,
                    "history_normal_labels_assumed": False,
                    "other_rules_recalled": False,
                    "raw_logs_persisted": False,
                    "material_source": case.get("source", {}).get(
                        "kind", "unspecified"
                    ),
                    "observed_live_surge": signal["mode"] == "live",
                }
            ),
        )
        # Scope comes from all current envelopes, never from model-selected samples.
        case = {**case, "coverage": {**case["coverage"], "target_executeType": values}}
        calls, draft, generation = 0, None, None
        for phase in ("generate", "review"):
            try:
                selection = surge_sampling.select(
                    case,
                    self.budget,
                    operator_context=self.operator_context,
                    draft=draft,
                    generation=generation,
                    request_builder=self.ai.build_request,
                )
                request = selection["request"]
            except (ValueError, TypeError) as error:
                return self._finish(
                    folder,
                    signal,
                    job_id,
                    "blocked",
                    calls=calls,
                    reason="topic_request_not_ready",
                    error_type=type(error).__name__,
                )
            _write(
                folder / (phase + ".started.json"),
                {
                    "phase": phase,
                    "model": self.model,
                    "prompt_version": self.ai.PROMPT_VERSION,
                    "prompt_sha256": request["prompt_sha256"],
                    "started_at": _now(),
                },
            )
            calls += 1
            response = None
            try:
                response = self.generate(request)
                transport = response["transport"]
                if (
                    transport.get("error_code")
                    or transport.get("finish_reason") != "stop"
                ):
                    raise ValueError("incomplete_or_failed_model_response")
                parsed = self.ai.parse_reply(response.get("content"), request=request)
            except Exception as error:
                diagnostic = (
                    error.code
                    if isinstance(
                        error, (base.ReplyValidationError, topic_ai.TopicContractError)
                    )
                    else "model_response_not_usable"
                )
                _write(
                    folder / (phase + ".failed.json"),
                    {
                        "phase": phase,
                        "error_type": type(error).__name__,
                        "reason": "model_response_not_usable",
                        "error_code": diagnostic,
                        "json_diagnostics": getattr(error, "diagnostics", None)
                        if isinstance(
                            error,
                            (base.ReplyValidationError, topic_ai.TopicContractError),
                        )
                        else None,
                        "reply_diagnostics": getattr(error, "reply_diagnostics", None)
                        if isinstance(
                            error,
                            (base.ReplyValidationError, topic_ai.TopicContractError),
                        )
                        else None,
                        "reply_sha256": _hash(response.get("content"))
                        if isinstance(response, dict)
                        else None,
                    },
                )
                return self._finish(
                    folder,
                    signal,
                    job_id,
                    "blocked",
                    calls=calls,
                    reason=diagnostic,
                    ai_review_completed=False,
                    reply_diagnostics=getattr(error, "reply_diagnostics", None)
                    if isinstance(
                        error, (base.ReplyValidationError, topic_ai.TopicContractError)
                    )
                    else None,
                    json_diagnostics=getattr(error, "diagnostics", None)
                    if isinstance(
                        error, (base.ReplyValidationError, topic_ai.TopicContractError)
                    )
                    else None,
                )
            summary = {
                "phase": phase,
                "status": parsed["status"],
                "prompt_version": self.ai.PROMPT_VERSION,
                "prompt_sha256": request["prompt_sha256"],
                "reply_sha256": _hash(response["content"]),
                "selection": selection["selection"],
                "transport": {
                    k: transport[k] for k in TRANSPORT_FIELDS if k in transport
                },
                "validation_issues": parsed["validation_issues"],
                "topic_submission_ready": parsed["topic_submission_ready"],
                "json_diagnostics": parsed["json_diagnostics"],
                "reply_diagnostics": parsed["reply_diagnostics"],
                "format_valid": parsed["format_valid"],
                "expression_repair_required": parsed["expression_repair_required"],
            }
            if "tree_compilation" in parsed:
                summary["tree_compilation"] = parsed["tree_compilation"]
            _write(folder / (phase + ".summary.json"), _compact(summary))
            if phase == "generate":
                draft, generation = parsed, selection
        # The final answer is already bounded; retain no raw model response, full
        # prompt, full originals, or prior model answers in the production record.
        final = {key: copy.deepcopy(parsed[key]) for key in SUMMARY_FIELDS}
        candidate = _compact(
            {
                "schema_version": SCHEMA,
                "job_id": job_id,
                "created_at": _now(),
                "company_code": scope["company_code"],
                "topic": scope["topic"],
                "trigger_rule_code": scope["rule_code"],
                "trigger_rule_version": case["rule"]["rule_config_version"],
                "trigger_rule_sha256": _hash(case.get("original_rule", case["rule"])),
                "model": self.model,
                "optimization_scope": topic_ai.SCOPE,
                "material_source": case.get("source", {}).get("kind", "unspecified"),
                "observed_live_surge": signal["mode"] == "live",
                "executeType": values,
                "execute_type_basis": execute.get("basis"),
                "analysis_status": parsed["status"],
                "ai_review_completed": True,
                "recommendation_ready": parsed["topic_submission_ready"],
                "filter": parsed["filter"],
                "explanation": final,
                "evidence": _evidence(parsed, selection),
                "sample_alert_ids": parsed["sample_alert_ids"],
                "filter_replay_performed": False,
                "filter_coverage_measured": False,
                "semantic_quality_verified": False,
                "candidate_engine_availability_verified": False,
                "activation_state": "managed_by_zeus_not_verified",
            }
        )
        if "tree_compilation" in parsed:
            candidate["tree_compilation"] = parsed["tree_compilation"]
        _write(folder / "recommendation.json", _compact(candidate))
        _write(
            folder / "review-authorization.json",
            {
                "job_id": job_id,
                "signal_sha256": _hash(signal),
                "candidate_sha256": _hash(candidate),
                "review_summary_sha256": hashlib.sha256(
                    _read_bytes(folder / "review.summary.json")
                ).hexdigest(),
                "generate_summary_sha256": hashlib.sha256(
                    _read_bytes(folder / "generate.summary.json")
                ).hexdigest(),
            },
        )
        return self._deliver(folder, signal, job_id, candidate, calls=calls)

    def _deliver(self, folder, signal, job_id, candidate, *, calls):
        self._verify_candidate(folder, signal, job_id, candidate)
        if candidate["analysis_status"] != "proposal":
            return self._finish(
                folder,
                signal,
                job_id,
                "not_applicable",
                calls=calls,
                reason="topic_review_without_proposal",
                ai_review_completed=True,
                recommendation_ready=False,
            )
        if not candidate["recommendation_ready"]:
            return self._finish(
                folder,
                signal,
                job_id,
                "manual_review",
                calls=calls,
                reason="topic_review_withheld",
                ai_review_completed=True,
                recommendation_ready=False,
            )
        current = self.load_rules().get_rule(
            signal["rule_code"],
            topic=signal["topic"],
            company_code=signal["company_code"],
        )
        if _hash(current) != candidate["trigger_rule_sha256"]:
            return self._finish(
                folder,
                signal,
                job_id,
                "manual_review",
                calls=calls,
                reason="trigger_rule_changed_since_analysis",
                ai_review_completed=True,
            )
        payload = {
            **copy.deepcopy(candidate["filter"]),
            "executeType": candidate["executeType"],
        }
        delivery = self.delivery.deliver(
            payload,
            company_code=signal["company_code"],
            scope=self.delivery_scope or signal["mode"] + ":" + signal["namespace"],
        )
        if delivery["status"] == "busy":
            return self._outcome(folder, job_id, "busy", calls=calls)
        delivery_path = folder / "delivery.json"
        if delivery_path.exists():
            saved = _read(delivery_path)

            # A crash after receipt publication but before job finalization is
            # resumed from the same successful ledger entry, now called duplicate.
            def comparable(row):
                return {k: v for k, v in row.items() if k not in {"status", "reason"}}

            same_success = (
                saved.get("status") in {"submitted", "duplicate"}
                and delivery["status"] in {"submitted", "duplicate"}
                and comparable(saved) == comparable(delivery)
            )
            if saved != delivery and not same_success:
                raise DurableSurgeError("topic_saved_delivery_conflict")
            delivery = saved
        _write(delivery_path, _compact(delivery))
        accepted = delivery["status"] in {"submitted", "duplicate"}
        return self._finish(
            folder,
            signal,
            job_id,
            "received" if accepted else "blocked",
            calls=calls,
            reason="zeus_topic_rule_received"
            if accepted
            else "zeus_submission_needs_attention",
            ai_review_completed=True,
            recommendation_ready=True,
            zeus_delivery_status=delivery["status"],
            zeus_rule_id=delivery.get("rule_id"),
        )

    def _verify_candidate(self, folder, signal, job_id, candidate):
        expected = {
            "job_id": job_id,
            "signal_sha256": _hash(signal),
            "candidate_sha256": _hash(candidate),
            "review_summary_sha256": hashlib.sha256(
                _read_bytes(folder / "review.summary.json")
            ).hexdigest(),
            "generate_summary_sha256": hashlib.sha256(
                _read_bytes(folder / "generate.summary.json")
            ).hexdigest(),
        }
        authorization = folder / "review-authorization.json"
        if not authorization.exists() or _read(authorization) != expected:
            raise DurableSurgeError("topic_candidate_not_bound_to_review")
        if (
            candidate.get("schema_version") != SCHEMA
            or candidate.get("job_id") != job_id
            or candidate.get("topic") != signal["topic"]
            or candidate.get("company_code") != signal["company_code"]
            or candidate.get("trigger_rule_code") != signal["rule_code"]
        ):
            raise DurableSurgeError("topic_candidate_scope_mismatch")
        review = _read(folder / "review.summary.json")
        if candidate.get("recommendation_ready") != review.get(
            "topic_submission_ready"
        ) or candidate.get("analysis_status") != review.get("status"):
            raise DurableSurgeError("topic_candidate_review_decision_mismatch")

    def _finish(self, folder, signal, job_id, status, *, calls, **fields):
        outcome = self._outcome(folder, job_id, status, calls=calls, **fields)
        artifacts = {
            p.name: hashlib.sha256(_read_bytes(p)).hexdigest()
            for p in sorted(folder.glob("*.json"))
            if p.name != "result.json"
        }
        record = {
            "schema_version": SCHEMA,
            "signal_sha256": _hash(signal),
            "outcome": {k: v for k, v in outcome.items() if k not in ATTEMPT_KEYS},
            "artifacts": artifacts,
            "artifact_bytes": sum(p.stat().st_size for p in folder.glob("*.json")),
        }
        record["record_id"] = _hash(record)
        _write(folder / "result.json", _compact(record))
        return {**outcome, "record_id": record["record_id"]}

    def verify_durable_result(self, signal, outcome):
        _, _, job_id = _identity(signal)
        folder = self.directory / "jobs" / job_id
        record = _read(folder / "result.json")
        if (
            record.get("schema_version") != SCHEMA
            or record.get("signal_sha256") != _hash(signal)
            or record["record_id"]
            != _hash({k: v for k, v in record.items() if k != "record_id"})
        ):
            raise DurableSurgeError("topic_result_integrity_failed")
        expected = {**record["outcome"], "record_id": record["record_id"]}
        if {k: v for k, v in outcome.items() if k not in ATTEMPT_KEYS} != expected:
            raise DurableSurgeError("topic_outcome_mismatch")
        for name, digest in record["artifacts"].items():
            if (
                Path(name).name != name
                or hashlib.sha256(_read_bytes(folder / name)).hexdigest() != digest
            ):
                raise DurableSurgeError("topic_artifact_changed")
        return True
