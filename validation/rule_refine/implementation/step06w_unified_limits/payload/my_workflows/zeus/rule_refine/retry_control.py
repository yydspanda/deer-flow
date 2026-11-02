"""Local failed-signal maintenance; explicit retries preserve every old attempt."""

import hashlib
from pathlib import Path

from .background_tick import _locked, _reset_locked
from .materials import _encode
from .pending_signal import SCOPE, _durable_result
from .signal_processing import SignalProcessor, _load, _result_unknown


def _unused(*args, **kwargs):
    raise RuntimeError("maintenance_does_not_run_analysis")


def _processor(directory, source_kind):
    return SignalProcessor(
        directory,
        load_rules=_unused,
        load_task_rows=_unused,
        analyze=_unused,
        sink=None,
        source_kind=source_kind,
    )


def _inspect(folder, processor):
    data = _load(folder / "input.json")
    signal = data["signal"]
    current, number, _ = processor._attempt(folder)
    current = folder if current is None else current
    result, started = current / "analysis.json", current / "analysis_started.json"
    if result.exists():
        bundle = processor._saved_attempt(current, signal)
        status = bundle["analysis"].get("status")
        if _result_unknown(bundle["analysis"]):
            status = "unconfirmed"
        elif status == "analysis_failed":
            status = "failed"
        else:
            from .saved_delivery import _bundle

            record_id = hashlib.sha256(
                _bundle(bundle["materials"], bundle["analysis"])
            ).hexdigest()
            directory = processor.store.directory / "attempts" / record_id
            paths = list(directory.glob("*.result.json")) if directory.exists() else []
            last = (
                _load(max(paths, key=lambda p: p.stat().st_mtime_ns)) if paths else {}
            )
            status = (
                "blocked"
                if last.get("status") == "blocked"
                else "completed"
                if last.get("status") in {"received", "not_applicable", "manual_review"}
                else "delivery_pending"
            )
    elif started.exists():
        status = "unconfirmed"
    elif number:
        status = "scheduled"
    else:
        status = "materials_wait"
    return data, {
        "rule_code": signal["rule_code"],
        "topic": signal["topic"],
        "company_code": signal["company_code"],
        "status": status,
        "attempt_number": number,
        "released": (current / "released.json").exists(),
        "retry_available": status
        in {"failed", "blocked", "unconfirmed", "scheduled", "delivery_pending"},
    }


def maintain(
    directory,
    scheduler,
    *,
    action="list",
    rule=None,
    topic=None,
    company=None,
    all_rules=False,
    allow_unconfirmed=False,
    counter=None,
):
    if action not in {"list", "retry", "release"}:
        raise ValueError("unknown_maintenance_action")
    if action != "list" and (not all_rules and not rule):
        raise ValueError("select_rule_or_all_explicitly")
    if action != "list" and counter is None:
        raise ValueError("counter_required_for_explicit_action")
    arguments = dict(
        action=action,
        rule=rule,
        topic=topic,
        company=company,
        all_rules=all_rules,
        allow_unconfirmed=allow_unconfirmed,
        counter=counter,
    )
    if action == "list":
        return _maintain_locked(directory, None, **arguments)
    with _locked(scheduler) as state_path:
        if state_path is None:
            return {
                "status": "busy",
                "reason": "background_tick_in_progress",
                "items": [],
                "model_calls": 0,
                "automatic_apply": False,
            }
        return _maintain_locked(directory, state_path, **arguments)


def _maintain_locked(
    directory,
    state_path,
    *,
    action,
    rule,
    topic,
    company,
    all_rules,
    allow_unconfirmed,
    counter,
):
    directory = Path(directory).expanduser().resolve()
    jobs = directory / "jobs"
    latest = {}
    for folder in sorted(jobs.iterdir()) if jobs.exists() else []:
        if (
            folder.is_symlink()
            or not folder.is_dir()
            or not (folder / "input.json").is_file()
        ):
            continue
        data = _load(folder / "input.json")
        signal = data["signal"]
        if (
            (rule and signal.get("rule_code") != rule)
            or (topic and signal.get("topic") != topic)
            or (company and signal.get("company_code") != company)
        ):
            continue
        processor = _processor(directory, data["source_kind"])
        data, row = _inspect(folder, processor)
        key = tuple(signal[name] for name in ("mode", "namespace", *SCOPE))
        previous = latest.get(key)
        if previous is None or signal.get("window_end", "") >= previous[0][
            "signal"
        ].get("window_end", ""):
            latest[key] = (data, row, processor)
    # Do not resurrect an older failure after a later signal already succeeded.
    selected = [entry for entry in latest.values() if entry[1]["status"] != "completed"]
    if (
        not all_rules
        and action != "list"
        and len(
            {(r["company_code"], r["topic"], r["rule_code"]) for _, r, _ in selected}
        )
        > 1
    ):
        raise ValueError("ambiguous_rule_use_topic_and_company")
    if action == "list":
        return {
            "status": "listed",
            "items": [row for _, row, _ in selected],
            "model_calls": 0,
            "redis_calls": 0,
            "automatic_apply": False,
        }
    # Only the newest job per scope may be restored; older failed jobs are visible,
    # but restoring all of them would compete for the single pending slot.
    scopes = {}
    for data, row, processor in selected:
        key = tuple(data["signal"][name] for name in SCOPE)
        previous = scopes.get(key)
        if previous is None or data["signal"].get("window_end", "") > previous[0][
            "signal"
        ].get("window_end", ""):
            scopes[key] = (data, row, processor)
    return _apply_locked(scopes, state_path, action, counter, allow_unconfirmed)


def _apply_locked(scopes, state_path, action, counter, allow_unconfirmed):
    # run_tick holds this same lock through acknowledgement, so it cannot delete
    # a restored signal after this explicit retry has been scheduled.
    outcomes = []
    for data, row, processor in scopes.values():
        signal = data["signal"]
        if (
            signal.get("mode") != counter.mode
            or signal.get("namespace") != counter.namespace
        ):
            raise ValueError("maintenance_counter_scope_mismatch")
        observation = {
            "mode": counter.mode,
            "company_scope_source": "rule_config_unique_match",
            **{name: signal[name] for name in SCOPE},
        }
        current = counter.pending(observation)
        if current is not None and current != signal:
            outcomes.append({**row, "action_status": "newer_pending_preserved"})
            continue
        if row["status"] == "unconfirmed" and not allow_unconfirmed:
            outcomes.append({**row, "action_status": "requires_allow_unconfirmed"})
            continue
        if action == "retry":
            if not row["retry_available"]:
                outcomes.append({**row, "action_status": "no_failed_analysis"})
                continue
            result = processor.request_retry(
                signal, allow_unconfirmed=allow_unconfirmed
            )
            if result["status"] == "busy":
                outcomes.append({**row, "action_status": "busy"})
                continue
            if current is None:
                # Explicit operator restoration only; never overwrite a new signal.
                restored = counter.client.set(
                    counter._keys(observation)[2],
                    _encode(signal, 65536).decode("utf-8"),
                    nx=True,
                )
                if not restored and counter.pending(observation) != signal:
                    outcomes.append({**row, "action_status": "newer_pending_preserved"})
                    continue
            scope_key = hashlib.sha256(
                _encode(
                    {
                        name: signal[name]
                        for name in ("company_code", "topic", "rule_code")
                    },
                    8192,
                )
            ).hexdigest()
            reset = _reset_locked(state_path, scopes={scope_key})
            outcomes.append(
                {
                    **row,
                    "action_status": result["status"],
                    "new_attempt": result["attempt_number"],
                    "scheduler_status": reset["status"],
                    "next_background_tick_may_call_model": result.get(
                        "may_call_model_on_next_tick", True
                    ),
                }
            )
        else:
            result = processor.release(signal, allow_unconfirmed=allow_unconfirmed)
            if result["status"] == "busy":
                outcomes.append({**row, "action_status": "busy"})
                continue
            _durable_result(processor, signal, result)
            applied = current is None or counter.acknowledge(
                observation, signal["signal_id"]
            )
            outcomes.append(
                {
                    **row,
                    "action_status": "released" if applied else "pending_changed",
                    "may_call_model_on_next_tick": False,
                }
            )
    return {
        "status": "completed",
        "action": action,
        "items": outcomes,
        "model_calls": 0,
        "automatic_apply": False,
        "existing_results_preserved": True,
    }
