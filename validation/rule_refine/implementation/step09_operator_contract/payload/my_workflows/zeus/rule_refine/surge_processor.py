"""Durable, local-only two-context surge advice; never executes a filter.

Each signal owns one frozen case and at most one request per phase. An ambiguous
request is parked rather than repeated. All files containing source/model material
stay in the operator's private runtime directory, outside the source checkout.
"""

import copy
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import surge_ai_review as ai
from . import surge_sampling as sampling
from .surge_history import MaterialsPendingError

SCHEMA = "sec_know.rule_signal_processing.v1"
SCOPE_KEYS = (
    "mode",
    "namespace",
    "source_app",
    "company_code",
    "topic",
    "rule_code",
    "signal_id",
)
ATTEMPT_KEYS = {"analysis_reused", "model_calls_this_attempt"}


class DurableSurgeError(ValueError):
    """Saved evidence is incomplete, inconsistent, or belongs to another signal."""


def _hash(value):
    return hashlib.sha256(ai.encoded(value)).hexdigest()


def _read_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise DurableSurgeError("artifact_not_regular_file")
        return stream.read()


def _read(path):
    return json.loads(_read_bytes(path))


def _write(path, value, *, raw=False):
    """Publish an immutable file, fsyncing both bytes and the directory entry."""
    data = value if raw else ai.encoded(value)
    if os.path.lexists(path):
        if _read_bytes(path) != data:
            raise DurableSurgeError("immutable_artifact_conflict:" + path.name)
        return
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read_bytes(path) != data:
                raise DurableSurgeError("immutable_artifact_conflict:" + path.name)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.unlink(temporary)


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise DurableSurgeError("artifact_directory_invalid")


def _identity(signal):
    signal = json.loads(ai.encoded(signal))
    if (
        not isinstance(signal, dict)
        or signal.get("schema_version") != "sec_know.rule_burst_signal.v1"
    ):
        raise ValueError("burst_signal_required")
    scope = {key: signal.get(key) for key in SCOPE_KEYS}
    if any(not isinstance(value, str) or not value.strip() for value in scope.values()):
        raise ValueError("complete_signal_scope_required")
    return signal, scope, _hash(scope)


def _rule_snapshot(rule):
    # Include the actual original configuration, not only a version label.
    return {key: copy.deepcopy(rule.get(key)) for key in ai.RULE_KEYS}


class SurgeProcessor:
    def __init__(
        self,
        directory,
        *,
        load_case,
        load_rules,
        generate,
        model,
        budget,
        operator_context="",
    ):
        if not all(callable(item) for item in (load_case, load_rules, generate)):
            raise ValueError("callable_dependencies_required")
        if (
            not isinstance(model, str)
            or not model.strip()
            or not isinstance(operator_context, str)
        ):
            raise ValueError("model_and_operator_context_required")
        self.directory = Path(directory).expanduser().resolve()
        self.load_case, self.load_rules, self.generate = load_case, load_rules, generate
        self.model, self.budget, self.operator_context = model, budget, operator_context

    @contextmanager
    def _job(self, signal):
        signal, scope, job_id = _identity(signal)
        _mkdir(self.directory)
        _mkdir(self.directory / "jobs")
        folder = self.directory / "jobs" / job_id
        _mkdir(folder)
        fd = os.open(
            folder / "process.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "rb") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise DurableSurgeError("invalid_lock_file")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield folder, signal, scope, job_id, False
                return
            try:
                yield folder, signal, scope, job_id, True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _outcome(self, folder, job_id, status, *, calls=0, reused=False, **fields):
        return {
            "schema_version": SCHEMA,
            "status": status,
            "job_id": job_id,
            "output_dir": str(folder),
            "automatic_apply": False,
            "model_calls_this_attempt": calls,
            "analysis_reused": reused,
            **fields,
        }

    def process(self, signal):
        with self._job(signal) as (folder, signal, scope, job_id, acquired):
            if not acquired:
                return self._outcome(folder, job_id, "busy")
            return self._process(folder, signal, scope, job_id)

    def _process(self, folder, signal, scope, job_id):
        input_path = folder / "input.json"
        if input_path.exists():
            frozen = _read(input_path)
            if frozen.get("signal") != signal or frozen.get("scope") != scope:
                raise DurableSurgeError("signal_identity_reused_with_different_content")
        else:
            frozen = {
                "schema_version": "sec_know.rule_surge_input.v1",
                "signal": signal,
                "scope": scope,
                "model": self.model,
                "budget": self.budget.audit(),
                "operator_context": self.operator_context,
            }
            _write(input_path, frozen)
        if (folder / "result.json").exists():
            saved = _read(folder / "result.json")
            outcome = {
                **saved["outcome"],
                "record_id": saved["record_id"],
                "analysis_reused": True,
                "model_calls_this_attempt": 0,
            }
            self.verify_durable_result(signal, outcome)
            return outcome
        if (frozen["model"], frozen["budget"], frozen["operator_context"]) != (
            self.model,
            self.budget.audit(),
            self.operator_context,
        ):
            return self._outcome(
                folder,
                job_id,
                "blocked",
                reason="processor_configuration_changed",
            )
        case_path = folder / "case.json"
        if not case_path.exists():
            try:
                case = json.loads(ai.encoded(self.load_case(copy.deepcopy(signal))))
            except MaterialsPendingError as error:
                return self._outcome(
                    folder,
                    job_id,
                    "materials_pending",
                    reason=error.code,
                    error_type=type(error).__name__,
                )
            except Exception as error:
                return self._outcome(
                    folder,
                    job_id,
                    "blocked",
                    reason="case_source_unavailable",
                    error_type=type(error).__name__,
                )
            if not case.get("current_samples"):
                return self._outcome(
                    folder,
                    job_id,
                    "materials_pending",
                    reason="current_materials_pending",
                )
            self._validate_case(case, scope)
            _write(case_path, case)
        case = _read(case_path)
        self._validate_case(case, scope)
        phases, calls, reused = [], 0, False
        generation = None
        for phase in ("generate", "review"):
            phase_dir = folder / phase
            _mkdir(phase_dir)
            selection_path = phase_dir / "selection.json"
            if selection_path.exists():
                selection = _read(selection_path)
            else:
                try:
                    selection = sampling.select(
                        case,
                        self.budget,
                        operator_context=self.operator_context,
                        draft=phases[0]["reply"] if phases else None,
                        generation=generation,
                        independent_initial=True,
                    )
                except ValueError as error:
                    return self._finalize(
                        folder,
                        signal,
                        job_id,
                        "blocked",
                        phases,
                        calls,
                        reused,
                        reason="sampling_failed",
                        error_type=type(error).__name__,
                    )
                _write(selection_path, selection)
            if phase == "generate":
                generation = selection
            request = selection["request"]
            self._verify_selection(selection, case)
            if (
                not (phase_dir / "started.json").exists()
                and not (phase_dir / "response.json").exists()
                and ai.request_operator_contract_version(request) is None
            ):
                # Saved evidence remains readable, but do not send an obsolete
                # prompt as a new request after the operator contract changed.
                return self._outcome(
                    folder,
                    job_id,
                    "blocked",
                    calls=calls,
                    reused=reused,
                    reason="saved_request_requires_new_operator_job",
                    phase=phase,
                )
            _write(phase_dir / "request.json", request)
            phase_result, phase_calls, phase_reused = self._phase(
                phase_dir, request, case["rule"], selection, phase
            )
            calls += phase_calls
            reused = reused or phase_reused
            if phase_result.get("status") == "analysis_unconfirmed":
                return self._outcome(
                    folder,
                    job_id,
                    "analysis_unconfirmed",
                    calls=calls,
                    reused=reused,
                    reason="request_started_without_saved_response",
                    phase=phase,
                )
            phases.append({"phase": phase, **phase_result})
            if "reply" not in phase_result:
                return self._finalize(
                    folder,
                    signal,
                    job_id,
                    "blocked",
                    phases,
                    calls,
                    reused,
                    reason=phase_result["error_code"],
                )
        final = phases[-1]["reply"]
        status = "received" if final["status"] == "proposal" else "not_applicable"
        reason = "local_ai_review_saved"
        if final["validation_issues"]:
            status, reason = "manual_review", "draft_needs_revision"
        try:
            current = self.load_rules().get_rule(
                scope["rule_code"],
                topic=scope["topic"],
                company_code=scope["company_code"],
            )
            unchanged = _rule_snapshot(current) == _rule_snapshot(
                case.get("original_rule", case["rule"])
            )
        except Exception as error:
            return self._finalize(
                folder,
                signal,
                job_id,
                "manual_review",
                phases,
                calls,
                reused,
                reason="configuration_unavailable",
                error_type=type(error).__name__,
            )
        if not unchanged:
            status, reason = "manual_review", "configuration_changed"
        _write(
            folder / "configuration-check.json",
            {
                "unchanged": unchanged,
                "expected_sha256": _hash(
                    _rule_snapshot(case.get("original_rule", case["rule"]))
                ),
                "actual_sha256": _hash(_rule_snapshot(current)),
            },
        )
        return self._finalize(
            folder, signal, job_id, status, phases, calls, reused, reason=reason
        )

    def _validate_case(self, case, scope):
        if not isinstance(case, dict) or not isinstance(case.get("coverage"), dict):
            raise DurableSurgeError("invalid_case")
        for key in ("rule", "original_rule"):
            rule = case.get(key, case.get("rule"))
            if not isinstance(rule, dict) or any(
                rule.get(field) != scope[field]
                for field in ("company_code", "topic", "rule_code")
            ):
                raise DurableSurgeError("case_rule_scope_mismatch")
        current = ai._samples(case["current_samples"])
        history = ai._samples(case["history_samples"])
        current_ids = [sample["alert_id"] for sample in current]
        history_ids = [sample["alert_id"] for sample in history]
        if len(set(current_ids + history_ids)) != len(current_ids + history_ids):
            raise DurableSurgeError("duplicate_or_overlapping_case_identities")
        if not current or any(not sample["raw_logs"] for sample in current + history):
            raise DurableSurgeError("case_logs_required")

    def _phase(self, folder, request, rule, selection, phase):
        started_path, response_path = folder / "started.json", folder / "response.json"
        started = {"request_sha256": _hash(request), "model": self.model}
        calls, reused = 0, response_path.exists()
        if started_path.exists():
            if _read(started_path) != started:
                raise DurableSurgeError("phase_start_request_mismatch")
            if not response_path.exists():
                return {"status": "analysis_unconfirmed"}, 0, False
        elif response_path.exists():
            raise DurableSurgeError("response_without_request_start")
        else:
            _write(started_path, started)
            calls = 1
            try:
                response = self.generate(copy.deepcopy(request))
                if not isinstance(response, dict) or not isinstance(
                    response.get("transport"), dict
                ):
                    raise ValueError("invalid_model_adapter_result")
                if response.get("content") is not None and not isinstance(
                    response["content"], str
                ):
                    raise ValueError("invalid_model_content_type")
                _write(
                    response_path,
                    {
                        "request_sha256": _hash(request),
                        "content": response.get("content"),
                        "transport": response["transport"],
                    },
                )
            except Exception as error:
                # A transport exception does not prove the upstream did not run.
                _write(
                    folder / "exception.json",
                    {
                        "error_type": type(error).__name__,
                        "request_sha256": _hash(request),
                    },
                )
                return {"status": "analysis_unconfirmed"}, calls, False
        response = _read(response_path)
        if response.get("request_sha256") != _hash(request):
            raise DurableSurgeError("response_request_mismatch")
        result = {
            "request_sha256": _hash(request),
            "response_sha256": _hash(response),
            "selection_sha256": _hash(selection),
        }
        transport = response["transport"]
        if not isinstance(response["content"], str) or transport.get("error_code"):
            result.update(status="analysis_failed", error_code="model_request_failed")
        elif transport.get("finish_reason") not in (None, "stop"):
            result.update(
                status="analysis_failed", error_code="model_response_not_complete"
            )
        else:
            try:
                reply = ai.parse_reply(
                    response["content"],
                    rule=rule,
                    visible_samples=selection["current_samples"]
                    + selection["history_samples"],
                    phase=phase,
                    field_usage_policy_version=ai.request_field_usage_version(request),
                    operator_contract_version=ai.request_operator_contract_version(
                        request
                    ),
                )
                result.update(status=reply["status"], reply=reply)
            except ai.ReplyValidationError as error:
                result.update(
                    status="analysis_failed",
                    error_code=error.code,
                    error_path=error.path,
                )
        # A process crash here can resume from response.json without another call.
        _write(folder / "parsed.json", result)
        return result, calls, reused

    def _verify_selection(self, selection, case):
        request = selection["request"]
        if (
            request["prompt_sha256"] != _hash(request["messages"])
            or json.loads(request["messages"][1]["content"]) != request["context"]
        ):
            raise DurableSurgeError("prompt_context_mismatch")
        if request["context"]["rule"] != ai._rule(case["rule"]):
            raise DurableSurgeError("prompt_rule_mismatch")
        try:
            ai.request_field_usage_version(request)
            ai.request_operator_contract_version(request)
        except ai.ReplyValidationError as error:
            raise DurableSurgeError(error.code) from error
        for sample_key in ("current_samples", "history_samples"):
            originals = {sample["alert_id"]: sample for sample in case[sample_key]}
            if any(
                originals.get(sample["alert_id"]) != sample
                for sample in selection[sample_key]
            ) or request["context"][sample_key] != ai._samples(selection[sample_key]):
                raise DurableSurgeError("selected_evidence_mismatch")

    def _review_method(self, folder):
        """Describe the frozen request, not a proof of the model's judgment."""
        path = folder / "review" / "request.json"
        if not path.exists():
            return {}
        context = _read(path).get("context", {})
        if context.get("scope_policy_version") != ai.SCOPE_POLICY_VERSION:
            # Old published results and interrupted reports remain byte-identical.
            return {}
        method = {
            "scope_policy_version": context["scope_policy_version"],
            "review_method": context["review_method"],
            "draft_explanation_included": context["draft_explanation_included"],
            "recommendation_ready_scope": "format_fields_and_references_only",
            "filter_scope_program_verified": False,
            "semantic_quality_verified": False,
        }
        # New claims belong only to requests that actually carried this policy.
        # Old 7D recommendations/reports remain byte-compatible on recovery.
        version = ai.request_field_usage_version(_read(path))
        if version is not None:
            method.update(
                field_usage_policy_version=version,
                recommendation_ready_scope="format_fields_references_and_reviewed_field_roles_only",
                detection_metadata_filter_check_performed=True,
                candidate_engine_availability_verified=False,
            )
        operator_version = ai.request_operator_contract_version(_read(path))
        if operator_version is not None:
            method["operator_contract_version"] = operator_version
        return method

    def _recommendation(self, folder, job_id, case, final, phases, ready):
        evidence = {}
        provenance = []
        for phase in phases:
            name = phase["phase"]
            selection = _read(folder / name / "selection.json")
            response = _read(folder / name / "response.json")
            evidence[name] = {
                "current_samples": selection["current_samples"],
                "history_samples": selection["history_samples"],
                "selection": selection["selection"],
                "cited_alert_ids": phase.get("reply", {}).get("sample_alert_ids", []),
            }
            provenance.append(
                {
                    "phase": name,
                    "prompt_sha256": selection["request"]["prompt_sha256"],
                    "request_sha256": phase["request_sha256"],
                    "response_sha256": phase["response_sha256"],
                    "transport": response["transport"],
                    "output_warnings": phase.get("reply", {}).get(
                        "output_warnings", []
                    ),
                }
            )
        return {
            "schema_version": "sec_know.rule_surge_recommendation.v1",
            "recommendation_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, "sec-know-rule-surge:" + job_id)
            ),
            "job_id": job_id,
            "rule": _rule_snapshot(case["rule"]),
            "original_rule_sha256": _hash(
                _rule_snapshot(case.get("original_rule", case["rule"]))
            ),
            "change_type": "add_exclusion",
            **{
                key: final.get(key)
                for key in (
                    "issue",
                    "recommendation",
                    "certainty",
                    "certainty_source",
                    "filter",
                    "review_notes",
                    "validation_issues",
                )
            },
            "evidence": evidence,
            "coverage": case["coverage"],
            "provenance": provenance,
            "recommendation_ready": ready,
            "ai_review_completed": True,
            "semantic_quality_verified": False,
            "validation_scope": final.get("validation_scope", ai.VALIDATION_SCOPE),
            "automatic_apply": False,
            "formal_recommendation_submitted": False,
            "delivery_scope": "local_record_only",
            "filter_replay_performed": False,
            "filter_coverage_measured": False,
            "engine_compatibility": "not_verified",
            **self._review_method(folder),
        }

    def _finalize(
        self, folder, signal, job_id, status, phases, calls, reused, **fields
    ):
        case = _read(folder / "case.json")
        final = phases[-1].get("reply") if phases else None
        reviewed = len(phases) == 2 and final is not None
        ready = reviewed and status == "received"
        if reviewed and final["status"] == "proposal":
            _write(
                folder / "recommendation.json",
                self._recommendation(folder, job_id, case, final, phases, ready),
            )
        report = [
            "# 规则告警突增过滤建议（本地记录）",
            "",
            "规则：" + signal["rule_code"] + "；状态：" + status + "。",
            "生成与复核使用分别保存的原始样本；历史对照不是正常业务标签。",
            "未执行过滤条件，未测量全量覆盖率或线上降量率，未提交或应用规则。",
            "",
        ]
        review_method = self._review_method(folder)
        if review_method:
            report.extend(
                [
                    "本轮复核任务：检查JSON实际排除范围及其他行为能否保留；未输入第一轮解释或判断把握。",
                    "格式、字段和引用检查通过仅表示可供人工审阅，不代表已证明条件不会误排其他行为。",
                    "",
                ]
            )
        if review_method.get("field_usage_policy_version"):
            report.extend(
                [
                    "已确认的规则命中名称、编号等检测结果仅用于背景理解，不允许作为新增排除条件。",
                    "其余可见字段是候选，不等于已确认在引擎执行时可用；规则引擎兼容性仍未验证。",
                    "",
                ]
            )
        for phase in phases:
            selection = _read(folder / phase["phase"] / "selection.json")["selection"]
            report.append(
                f"{phase['phase']}：当前 {selection['current_count']} 条、历史 {selection['history_count']} 条；状态 {phase['status']}。"
            )
            # Preserve byte-for-byte publication for previously frozen jobs,
            # including recovery after REPORT.md but before result.json.
            if phase["phase"] == "review" and selection.get("sampling_policy_version"):
                report.append(
                    f"复核新增原文 {selection['new_current_count']} 条；复用草案引用原文 {selection['cited_current_reused']} 条；"
                    f"未展示的草案当前引用 {selection['draft_current_references_not_displayed']} 条。"
                    "原草案完整保留，最终意见只能引用本轮已展示的原文。"
                )
                if not selection["new_current_count"]:
                    report.append(
                        "本轮没有增加新告警原文，属于同材料复核；不能据此声称已检查另一批告警。"
                    )
        if final:
            if review_method.get("field_usage_policy_version") and final.get(
                "validation_issues"
            ):
                report.extend(["", "## 条件仍需修改", ""])
                for issue in final["validation_issues"]:
                    field = issue.get("field", "")
                    path = ".".join(str(part) for part in issue.get("path", []))
                    if issue["code"] == "condition_uses_detection_metadata":
                        explanation = "属于已确认的检测结果信息，不能作为新增排除条件"
                    else:
                        explanation = issue["code"]
                    report.append(f"- {path}：`{field}` {explanation}。")
                report.append(
                    "原回答保留供排查；下列条件尚不可采纳，没有自动删除或替换条件。"
                )
            report.extend(
                [
                    "",
                    "## 问题",
                    "",
                    final["issue"],
                    "",
                    "## 建议及前提",
                    "",
                    final["recommendation"],
                    "",
                    final["certainty"],
                    "",
                    "## 条件建议",
                    "",
                    *(
                        [
                            "实际排除范围以以下条件及expression为准；说明文字不会自动增加命令、级别、来源等限制。",
                            "",
                        ]
                        if review_method
                        else []
                    ),
                    "```json",
                    json.dumps(final["filter"], ensure_ascii=False, indent=2),
                    "```",
                    "",
                    final.get("review_notes") or "",
                ]
            )
        if fields.get("reason"):
            report.extend(["", "处理说明：" + fields["reason"]])
        _write(
            folder / "REPORT.md", ("\n".join(report) + "\n").encode("utf-8"), raw=True
        )
        outcome = self._outcome(
            folder,
            job_id,
            status,
            reason=fields.pop("reason", None),
            analysis_status=final["status"] if final else "analysis_failed",
            ai_review_completed=reviewed,
            recommendation_ready=ready,
            filter_replay_performed=False,
            filter_coverage_measured=False,
            **review_method,
            **fields,
        )
        for key in ATTEMPT_KEYS:
            outcome.pop(key)
        files = ["input.json", "case.json", "REPORT.md"]
        files.extend(
            name
            for name in ("recommendation.json", "configuration-check.json")
            if (folder / name).exists()
        )
        for phase in ("generate", "review"):
            files.extend(
                phase + "/" + name
                for name in (
                    "selection.json",
                    "request.json",
                    "started.json",
                    "response.json",
                    "parsed.json",
                    "exception.json",
                )
                if (folder / phase / name).exists()
            )
        record = {
            "schema_version": "sec_know.rule_surge_result.v1",
            "job_id": job_id,
            "signal_sha256": _hash(signal),
            "outcome": outcome,
            "phases": phases,
            "artifacts": {
                name: hashlib.sha256(_read_bytes(folder / name)).hexdigest()
                for name in sorted(files)
            },
        }
        record["record_id"] = _hash(record)
        _write(folder / "result.json", record)
        result = {
            **outcome,
            "record_id": record["record_id"],
            "analysis_reused": reused,
            "model_calls_this_attempt": calls,
        }
        self.verify_durable_result(signal, result)
        return result

    def verify_durable_result(self, signal, outcome):
        """Verify a local result's exact signal and complete saved evidence chain."""
        signal, scope, job_id = _identity(signal)
        folder = self.directory / "jobs" / job_id
        if folder.is_symlink():
            raise DurableSurgeError("invalid_job_directory")
        record = _read(folder / "result.json")
        content = {key: value for key, value in record.items() if key != "record_id"}
        if (
            record.get("record_id") != _hash(content)
            or record.get("job_id") != job_id
            or record.get("signal_sha256") != _hash(signal)
        ):
            raise DurableSurgeError("result_identity_or_hash_mismatch")
        expected = {**record["outcome"], "record_id": record["record_id"]}
        if {
            key: value for key, value in outcome.items() if key not in ATTEMPT_KEYS
        } != expected:
            raise DurableSurgeError("outcome_mismatch")
        artifacts = record.get("artifacts", {})
        if not {"input.json", "case.json", "REPORT.md"}.issubset(artifacts):
            raise DurableSurgeError("incomplete_artifact_manifest")
        for name, digest in artifacts.items():
            path = Path(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or len(path.parts) > 2
                or any(
                    (folder / Path(*path.parts[:i])).is_symlink()
                    for i in range(1, len(path.parts) + 1)
                )
            ):
                raise DurableSurgeError("invalid_artifact_path")
            if hashlib.sha256(_read_bytes(folder / name)).hexdigest() != digest:
                raise DurableSurgeError("artifact_hash_mismatch:" + name)
        frozen, case = _read(folder / "input.json"), _read(folder / "case.json")
        if frozen.get("signal") != signal or frozen.get("scope") != scope:
            raise DurableSurgeError("saved_signal_mismatch")
        self._validate_case(case, scope)
        for phase in record["phases"]:
            name = phase["phase"]
            if name not in ("generate", "review"):
                raise DurableSurgeError("invalid_phase")
            required = {
                name + "/" + leaf
                for leaf in (
                    "selection.json",
                    "request.json",
                    "started.json",
                    "response.json",
                    "parsed.json",
                )
            }
            if not required.issubset(artifacts):
                raise DurableSurgeError("phase_artifacts_missing")
            selection, request, started, response, parsed = (
                _read(folder / name / leaf)
                for leaf in (
                    "selection.json",
                    "request.json",
                    "started.json",
                    "response.json",
                    "parsed.json",
                )
            )
            if (
                selection["request"] != request
                or started
                != {"request_sha256": _hash(request), "model": frozen["model"]}
                or response["request_sha256"] != _hash(request)
                or parsed
                != {key: value for key, value in phase.items() if key != "phase"}
            ):
                raise DurableSurgeError("phase_chain_mismatch")
            if (
                parsed["request_sha256"] != _hash(request)
                or parsed["response_sha256"] != _hash(response)
                or parsed["selection_sha256"] != _hash(selection)
            ):
                raise DurableSurgeError("phase_hash_link_mismatch")
            self._verify_selection(selection, case)
            if "reply" in parsed:
                if parsed["reply"] != ai.parse_reply(
                    response["content"],
                    rule=case["rule"],
                    visible_samples=selection["current_samples"]
                    + selection["history_samples"],
                    phase=name,
                    field_usage_policy_version=ai.request_field_usage_version(request),
                    operator_contract_version=ai.request_operator_contract_version(
                        request
                    ),
                ):
                    raise DurableSurgeError("parsed_response_mismatch")
        if outcome["status"] in ("received", "not_applicable"):
            if [phase["phase"] for phase in record["phases"]] != [
                "generate",
                "review",
            ] or any("reply" not in phase for phase in record["phases"]):
                raise DurableSurgeError("completed_review_required")
            if (
                "configuration-check.json" not in artifacts
                or _read(folder / "configuration-check.json")["unchanged"] is not True
            ):
                raise DurableSurgeError("configuration_confirmation_required")
        if outcome["status"] == "received":
            final = record["phases"][-1]["reply"]
            if final["status"] != "proposal" or final["validation_issues"]:
                raise DurableSurgeError("reviewable_proposal_required")
            if "recommendation.json" not in artifacts:
                raise DurableSurgeError("recommendation_missing")
            recommendation = _read(folder / "recommendation.json")
            wanted = self._recommendation(
                folder,
                job_id,
                case,
                record["phases"][-1]["reply"],
                record["phases"],
                True,
            )
            if recommendation != wanted:
                raise DurableSurgeError("recommendation_mismatch")
        return True
