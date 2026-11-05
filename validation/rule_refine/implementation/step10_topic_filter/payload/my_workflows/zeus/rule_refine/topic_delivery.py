"""Small, durable creation receipts; uncertain POSTs are never sent again."""

import copy
import fcntl
import hashlib
import os
import stat
from pathlib import Path

from .operators import OPERATORS
from .surge_ai_review import ALIAS, _expression, encoded
from .surge_processor import _mkdir, _read, _write

SCHEMA = "sec_know.topic_filter_delivery.v1"
MAX_PAYLOAD_BYTES = 64 * 1024
RESERVED = {"topic", "expression", "executeType"}
TERMINAL = {"submitted", "submission_unknown", "rejected"}


def _text(value, maximum):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and value == value.strip()
        and value.isprintable()
    )


def validate_payload(payload):
    """Validate wire structure without importing credentials or running a filter."""
    if not isinstance(payload, dict) or not RESERVED.issubset(payload):
        raise ValueError("topic_payload_required")
    if not _text(payload["topic"], 100):
        raise ValueError("invalid_topic")
    execute = payload["executeType"]
    if (
        not isinstance(execute, list)
        or len(execute) != 1
        or type(execute[0]) is not int
        or execute[0] not in {0, 1, 2, 3}
    ):
        raise ValueError("resolved_execute_type_required")
    aliases = set(payload) - RESERVED
    if not 1 <= len(aliases) <= 32 or any(
        not isinstance(name, str) or not ALIAS.fullmatch(name) for name in aliases
    ):
        raise ValueError("flat_condition_aliases_required")
    expression = payload["expression"]
    if not isinstance(expression, str) or len(expression) > 2048:
        raise ValueError("invalid_expression")
    if _expression(expression, aliases) != aliases:
        raise ValueError("unreferenced_condition")
    for alias in aliases:
        term = payload[alias]
        if not isinstance(term, dict) or set(term) != {"name", "value", "operator"}:
            raise ValueError("invalid_condition")
        if not _text(term["name"], 512):
            raise ValueError("invalid_condition_field")
        if (
            not isinstance(term["operator"], str)
            or term["operator"] not in OPERATORS
            or not isinstance(term["value"], str)
            or not term["value"]
            or len(term["value"]) > 4096
        ):
            raise ValueError("invalid_condition_value_or_operator")
    if len(encoded(payload)) > MAX_PAYLOAD_BYTES:
        raise ValueError("topic_payload_too_large")
    return copy.deepcopy(payload)


def _rule_id(value):
    return (type(value) is int and value > 0) or _text(value, 256)


class TopicDelivery:
    """One POST per exact payload/company/destination scope, across all jobs.

    The scope must identify the configured destination environment/namespace.
    This ledger does not claim semantic equivalence of differently written filters,
    server-side idempotence, or anything about ZEUS activation state.
    """

    def __init__(self, directory, submit):
        if not callable(submit):
            raise ValueError("submit_callable_required")
        self.directory = Path(directory).expanduser().absolute()
        self.submit = submit

    def deliver(self, payload, *, company_code, scope):
        payload = validate_payload(payload)
        if not _text(company_code, 512) or not _text(scope, 512):
            raise ValueError("delivery_company_and_scope_required")
        identity = {
            "company_code": company_code,
            "scope": scope,
            "payload": payload,
        }
        delivery_id = hashlib.sha256(encoded(identity)).hexdigest()
        payload_hash = hashlib.sha256(encoded(payload)).hexdigest()
        common = {
            "schema_version": SCHEMA,
            "delivery_id": delivery_id,
            "payload_sha256": payload_hash,
            "activation_state": "managed_by_zeus",
        }
        _mkdir(self.directory)
        folder = self.directory / delivery_id
        _mkdir(folder)
        fd = os.open(
            folder / "delivery.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        with os.fdopen(fd, "rb") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise ValueError("invalid_delivery_lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self._outcome(common, "busy", "delivery_in_progress")
            try:
                return self._locked(folder, common, identity)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _outcome(common, status, reason, *, code=None, rule_id=None, **extra):
        return {
            **common,
            "status": status,
            "reason": reason,
            "code": code,
            "rule_id": rule_id,
            **extra,
        }

    def _locked(self, folder, common, identity):
        started_path, result_path = folder / "started.json", folder / "result.json"
        started = {**common, **identity, "status": "request_started"}
        if os.path.lexists(result_path):
            if not os.path.lexists(started_path) or _read(started_path) != started:
                raise ValueError("delivery_identity_mismatch")
            saved = _read(result_path)
            if (
                not isinstance(saved, dict)
                or any(saved.get(key) != value for key, value in common.items())
                or saved.get("status") not in TERMINAL
                or set(saved) != {*common, "status", "reason", "code", "rule_id"}
                or (
                    saved["status"] == "submitted"
                    and (saved.get("code") != 200 or not _rule_id(saved.get("rule_id")))
                )
            ):
                raise ValueError("invalid_delivery_receipt")
            if saved["status"] == "submitted":
                return {**saved, "status": "duplicate", "reason": "already_submitted"}
            return saved
        if os.path.lexists(started_path):
            if _read(started_path) != started:
                raise ValueError("delivery_identity_mismatch")
            result = self._outcome(
                common, "submission_unknown", "previous_post_result_unknown"
            )
        else:
            # Persist and fsync before entering any external client code. Even a
            # process exit between this point and a response must not cause a retry.
            _write(started_path, started)
            try:
                response = self.submit(copy.deepcopy(identity["payload"]))
            except Exception:
                result = self._outcome(
                    common, "submission_unknown", "post_failed_result_unknown"
                )
            else:
                result = self._response(common, response)
        _write(result_path, result)
        return result

    def _response(self, common, response):
        if not isinstance(response, dict):
            return self._outcome(common, "submission_unknown", "invalid_response")
        code = response.get("code")
        if type(code) is not int or not 0 <= code <= 999999999:
            return self._outcome(common, "submission_unknown", "invalid_response_code")
        if code != 200:
            return self._outcome(common, "rejected", "zeus_rejected", code=code)
        if response.get("success") is not True or not _rule_id(response.get("rule_id")):
            return self._outcome(
                common, "submission_unknown", "success_receipt_incomplete", code=code
            )
        return self._outcome(
            common,
            "submitted",
            "zeus_received",
            code=code,
            rule_id=response["rule_id"],
        )
