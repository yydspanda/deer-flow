#!/usr/bin/env python3
"""Operator-directed DEV learning review via existing authenticated HTTP APIs only."""

from __future__ import annotations

import argparse
import copy
import csv
import fcntl
import hashlib
import io
import ipaddress
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE = "/api/soc/dev/corpus-workbench"
MEMORY = "/api/soc/memory/candidates"
PENDING = {"pending_review", "confirmed_candidate"}
PLAN_SCHEMA = "soc.dev_learning_bulk_review_plan.v1"


class BulkReviewError(RuntimeError):
    """An operator-visible bounded error, never a raw HTTP body."""


class ApiError(BulkReviewError):
    def __init__(self, status: int | None, detail: str = ""):
        self.status = status
        self.detail = detail
        super().__init__(
            f"HTTP {status}" if status else "HTTP connection/response outcome unknown"
        )


def validate_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    try:
        local = (
            parsed.hostname == "localhost"
            or ipaddress.ip_address(parsed.hostname or "").is_loopback
        )
    except ValueError:
        local = False
    if (
        parsed.scheme != "http"
        or not local
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise BulkReviewError(
            "URL must be an HTTP loopback origin, such as http://127.0.0.1:2026"
        )
    try:
        parsed.port
    except ValueError as exc:
        raise BulkReviewError("Invalid port") from exc
    return value.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ApiError(code, "Redirect refused")


class ApiClient:
    def __init__(self, url: str, timeout: float = 610.0):
        self.url = validate_url(url)
        self.timeout = max(610.0, timeout)
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, payload: dict, idempotency_key: str | None = None):
        return self._request("POST", path, payload, idempotency_key)

    def _request(self, method, path, payload=None, idempotency_key=None):
        if not path.startswith("/api/soc/") or path.startswith("//"):
            raise BulkReviewError("Invalid API path")
        headers = {
            "Accept": "application/json",
            "X-SOC-API-Version": "1",
            "x-soc-surface": "api",
        }
        for name, env in (
            ("Cookie", "SOC_REVIEW_COOKIE"),
            ("X-CSRF-Token", "SOC_REVIEW_CSRF_TOKEN"),
        ):
            if os.environ.get(env):
                headers[name] = os.environ[env]
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            with self._opener.open(
                urllib.request.Request(
                    self.url + path, data=data, headers=headers, method=method
                ),
                timeout=self.timeout,
            ) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ApiError(None, "Response too large")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Save bounded server detail privately; never print credentials or response bodies.
            raw = exc.read(32_768)
            try:
                detail = str(json.loads(raw).get("detail", "Request rejected"))[:4000]
            except (ValueError, AttributeError):
                detail = "Request rejected"
            raise ApiError(exc.code, detail) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ApiError(None, type(exc).__name__) from None


def canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _atomic(path: Path, value, *, text: bool = False):
    if path.is_symlink():
        raise BulkReviewError("Refusing a symlink output")
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(
                value
                if text
                else json.dumps(value, indent=2, ensure_ascii=False) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _locked(directory: Path):
    lock = directory / ".bulk-review.lock"
    if lock.is_symlink():
        raise BulkReviewError("Refusing a symlink lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BulkReviewError("Another process is using this review plan") from exc
        yield
    finally:
        os.close(fd)


def _load(path: Path):
    if path.is_symlink():
        raise BulkReviewError("Refusing a symlink input")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _candidate_path(candidate_id: str) -> str:
    if not isinstance(candidate_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", candidate_id
    ):
        raise BulkReviewError("Invalid candidate identity")
    return f"{MEMORY}/{candidate_id}"


def _idle_learning(client, expected_experiment=None):
    learning = client.get(f"{BASE}/quick-validation?batch=learning&limit=1")
    total = learning.get("total")
    if not isinstance(total, int) or total <= 0 or learning.get("completed") != total:
        raise BulkReviewError("The first learning batch must be fully completed")
    if any(learning.get(key) != 0 for key in ("failed", "active", "remaining")) or any(
        learning.get(key) is not False for key in ("running", "manual_pending")
    ):
        raise BulkReviewError("Pause and finish all learning work before review")
    experiment = learning.get("experiment_id")
    if (
        not isinstance(experiment, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", experiment)
        or (expected_experiment and experiment != expected_experiment)
    ):
        raise BulkReviewError("Current experiment does not match the review plan")
    validation = client.get(f"{BASE}/quick-validation?batch=validation&limit=1")
    if (
        validation.get("experiment_id") != experiment
        or any(validation.get(key) != 0 for key in ("completed", "failed", "active"))
        or any(
            validation.get(key) is not False for key in ("running", "manual_pending")
        )
    ):
        raise BulkReviewError("This tool requires a second batch that has not started")
    if validation.get("remaining") != validation.get("total"):
        raise BulkReviewError("Validation state is not untouched")
    return learning


def _inventory(client, experiment):
    entries, total = [], None
    while total is None or len(entries) < total:
        page = client.get(
            f"{BASE}/experiments/{experiment}/candidates?review_stage=pending&limit=100&offset={len(entries)}"
        )
        if (
            not isinstance(page, dict)
            or not isinstance(page.get("total"), int)
            or not isinstance(page.get("items"), list)
        ):
            raise BulkReviewError("Invalid candidate inventory response")
        if total is not None and total != page["total"]:
            raise BulkReviewError("Candidate inventory changed during pagination")
        total = page["total"]
        if total < 0 or total > 100_000 or (not page["items"] and len(entries) < total):
            raise BulkReviewError("Candidate pagination is incomplete")
        entries.extend(page["items"])
    ids = [item.get("candidate_id") for item in entries]
    if len(entries) != total or len(set(ids)) != len(ids):
        raise BulkReviewError("Duplicate or inconsistent candidate inventory")
    return {item["candidate_id"]: item for item in entries}


def _scope_identity(candidate):
    if (
        candidate.get("schema_version") != "soc.memory_candidate.v1"
        or candidate.get("tenant_id") != "pingan"
    ):
        raise BulkReviewError("Candidate is not a PingAn Memory candidate")
    spec = candidate.get("applicability") or {}
    environment = spec.get("required_facets", {}).get("environment")
    facets_environment = candidate.get("facets", {}).get("environment")
    if (environment if spec else facets_environment) != ["dev-corpus-eval"] or (
        facets_environment is not None and facets_environment != ["dev-corpus-eval"]
    ):
        raise BulkReviewError("Candidate is outside the DEV corpus scope")


def default_applicability(candidate):
    _scope_identity(candidate)
    if candidate.get("status") not in PENDING:
        raise BulkReviewError("Candidate is no longer pending")
    if candidate.get("decision_impact") != "detection_decision":
        raise BulkReviewError("Candidate does not allow exact decision reuse")
    if not isinstance(candidate.get("applicability"), dict):
        raise BulkReviewError("Candidate has no typed applicability")
    base = copy.deepcopy(candidate["applicability"])
    verified = (
        (candidate.get("scope_view") or {})
        .get("required_details", {})
        .get("behavior_fingerprint", {})
        .get("behavior_component")
    )
    selected = base.get("selected_behavior_components")
    if selected is None:
        selected = verified
    if (
        not isinstance(verified, list)
        or not verified
        or not isinstance(selected, list)
        or not selected
        or not all(
            isinstance(item, str) and item.strip() for item in [*verified, *selected]
        )
    ):
        raise BulkReviewError("Candidate has no verified default core behaviors")
    if not set(selected) <= set(verified):
        raise BulkReviewError("Default behavior selection is not verified")
    base["selected_behavior_components"] = sorted(set(selected))
    base["covered_behavior_components"] = base.get(
        "covered_behavior_components"
    ) or sorted(set(verified))
    base["policy_version"] = "soc.memory_applicability_policy.v4"
    return base


def build_plan(client, output_dir, expected_count, experiment_id=None):
    if not isinstance(expected_count, int) or expected_count <= 0:
        raise BulkReviewError("Expected candidate count must be positive")
    url = validate_url(client.url)
    learning = _idle_learning(client, experiment_id)
    experiment = learning["experiment_id"]
    inventory = _inventory(client, experiment)
    if (
        len(inventory) != expected_count
        or learning.get("pending_candidates") != expected_count
    ):
        raise BulkReviewError("Pending candidate count does not match --expected-count")
    items = []
    for cid, listed in inventory.items():
        candidate = client.get(_candidate_path(cid))
        if (
            candidate.get("candidate_id") != cid
            or candidate.get("status") not in PENDING
        ):
            raise BulkReviewError("Candidate identity/status changed while planning")
        _scope_identity(candidate)
        if canonical_hash(
            {k: v for k, v in candidate.items() if k != "scope_view"}
        ) != canonical_hash(listed):
            raise BulkReviewError("Candidate changed between inventory and detail")
        try:
            applicability, blocked = default_applicability(candidate), None
        except BulkReviewError as exc:
            applicability, blocked = None, str(exc)
        items.append(
            {
                "candidate_id": cid,
                "candidate_hash": canonical_hash(candidate),
                "candidate": candidate,
                "record_applicability": applicability,
                "blocked_reason": blocked,
            }
        )
    if canonical_hash(_inventory(client, experiment)) != canonical_hash(inventory):
        raise BulkReviewError("Candidate inventory changed before plan publication")
    _idle_learning(client, experiment)
    directory = Path(output_dir).expanduser().absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    plan = {
        "schema_version": PLAN_SCHEMA,
        "url": url,
        "experiment_id": experiment,
        "created_at": datetime.now(UTC).isoformat(),
        "expected_count": expected_count,
        "operator_verdict": "false_positive",
        "future_use": "exact_reuse",
        "items": items,
    }
    with _locked(directory):
        _atomic(directory / "plan.json", plan)
    print(
        f"Plan saved: {directory / 'plan.json'}; candidates={len(items)}, blocked={sum(bool(i['blocked_reason']) for i in items)}",
        flush=True,
    )
    return plan


def validate_draft(candidate_id, draft):
    if (
        not isinstance(draft, dict)
        or draft.get("schema_version") != "soc.memory_business_lesson_draft.v1"
        or draft.get("candidate_id") != candidate_id
        or draft.get("reviewer_verdict") != "false_positive"
    ):
        raise BulkReviewError("Draft identity or verdict mismatch")
    if (
        draft.get("decision_impact") != "none"
        or draft.get("review_required") is not True
        or draft.get("persistence_performed") is not False
    ):
        raise BulkReviewError(
            "Draft response does not preserve the non-authoritative drafting contract"
        )
    lesson = draft.get("lesson") or {}
    if lesson.get("schema_version") != "soc.memory_business_lesson.v2":
        raise BulkReviewError("Draft must contain a complete v2 business lesson")
    for key, minimum, maximum in (
        ("detection_scenario", 5, 2000),
        ("observed_event", 5, 4000),
        ("conclusion", 10, 2000),
    ):
        if (
            not isinstance(lesson.get(key), str)
            or not minimum <= len(lesson[key].strip()) <= maximum
        ):
            raise BulkReviewError(f"Draft field is incomplete: {key}")
    for key in (
        "business_rationale",
        "applicability_conditions",
        "generalization_boundaries",
        "invalidation_conditions",
        "handling_guidance",
    ):
        values = lesson.get(key)
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= (20 if key == "applicability_conditions" else 12)
            or any(
                not isinstance(v, str) or not 5 <= len(v.strip()) <= 4000
                for v in values
            )
        ):
            raise BulkReviewError(f"Draft section is incomplete: {key}")
    return lesson


def review_payload(candidate, applicability, draft, experiment_id, now=None):
    lesson = validate_draft(candidate["candidate_id"], draft)
    now = now or datetime.now(UTC)
    expires = now + timedelta(days=60)
    limit = (candidate.get("validity") or {}).get("valid_until")
    if candidate.get("source", {}).get("source_type") != "repeated_pattern" and limit:
        try:
            limit = datetime.fromisoformat(limit.replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            raise BulkReviewError("Invalid candidate validity") from exc
        if limit.tzinfo is None:
            raise BulkReviewError("Candidate validity has no timezone")
        if now + timedelta(minutes=1) < limit < expires:
            expires = limit - timedelta(seconds=30)
    if expires <= now + timedelta(days=30):
        raise BulkReviewError(
            "Candidate validity cannot cover the default review window"
        )
    return {
        "decision": "confirm",
        "reason": "审核人按本次 DEV 第一批批量审核指令统一选择误报，精确匹配时复用结论；保留页面默认的核心行为勾选条件。",
        "record_lesson": lesson,
        "record_applicability": applicability,
        "confirmed_verdict": "false_positive",
        "apply_to_future_matches": True,
        "clear_review_on_match": True,
        "activate_retrieval": True,
        "activation_valid_until": expires.isoformat(),
        "activation_review_after_days": 30,
        "metadata": {
            "source": "operator_bulk_review",
            "experiment_id": experiment_id,
            "batch": "learning",
            "individual_manual_review_claimed": False,
        },
    }


def _verify_result(cid, result):
    candidate, record = result.get("candidate") or {}, result.get("memory_record") or {}
    directive = record.get("decision_directive") or {}
    if (
        result.get("schema_version") != "soc.memory_candidate_review_result.v1"
        or result.get("decision") != "confirm"
        or candidate.get("candidate_id") != cid
        or candidate.get("status") != "confirmed"
        or record.get("source_candidate_id") != cid
        or record.get("status") != "confirmed"
        or record.get("retrieval_enabled") is not True
        or record.get("reviewed_verdict") != "false_positive"
        or directive.get("target_verdict") != "false_positive"
        or directive.get("effect") != "override"
        or directive.get("review_effect") != "clear"
    ):
        raise BulkReviewError(
            "Review response does not prove the requested published state"
        )
    if not record.get("memory_id"):
        raise BulkReviewError("Published Memory identity is missing")
    return record["memory_id"]


def _receipts(directory, plan, state):
    rows = [
        {
            "candidate_id": item["candidate_id"],
            "status": state["items"]
            .get(item["candidate_id"], {})
            .get("status", "pending"),
            "memory_id": state["items"].get(item["candidate_id"], {}).get("memory_id"),
            "error": state["items"].get(item["candidate_id"], {}).get("error"),
        }
        for item in plan["items"]
    ]
    result = {
        "schema_version": "soc.dev_learning_bulk_review_receipts.v1",
        "experiment_id": plan["experiment_id"],
        "plan_hash": canonical_hash(plan),
        "updated_at": datetime.now(UTC).isoformat(),
        "counts": dict(Counter(row["status"] for row in rows)),
        "items": rows,
    }
    _atomic(directory / "state.json", state)
    _atomic(directory / "receipts.json", result)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=["candidate_id", "status", "memory_id", "error"]
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: (
                    "'" + str(value)
                    if str(value or "").lstrip().startswith(("=", "+", "-", "@"))
                    else value
                )
                for key, value in row.items()
            }
        )
    _atomic(directory / "receipts.csv", buffer.getvalue(), text=True)
    return result


def _same_candidate(client, item):
    candidate = client.get(_candidate_path(item["candidate_id"]))
    if (
        candidate.get("status") not in PENDING
        or canonical_hash(candidate) != item["candidate_hash"]
    ):
        raise BulkReviewError("Candidate changed after planning; not reviewed")
    return candidate


def _saved_review(plan, item, saved):
    """Retain the exact immutable command across uncertain HTTP outcomes."""
    payload = saved["review_payload"]
    fixed = {
        "decision": "confirm",
        "confirmed_verdict": "false_positive",
        "apply_to_future_matches": True,
        "clear_review_on_match": True,
        "activate_retrieval": True,
        "activation_review_after_days": 30,
    }
    if (
        any(payload.get(key) != value for key, value in fixed.items())
        or payload.get("record_applicability") != item["record_applicability"]
        or payload.get("record_lesson")
        != validate_draft(item["candidate_id"], saved["draft"])
    ):
        raise BulkReviewError("Saved review command differs from the frozen plan")
    expected = "bulk-memory-" + canonical_hash(
        [canonical_hash(plan), item["candidate_id"], payload]
    )
    if saved.get("idempotency_key") != expected:
        raise BulkReviewError("Saved review payload or idempotency key changed")


def apply_plan(client, plan_path, retry_failed=False):
    path = Path(plan_path).expanduser().absolute()
    directory = path.parent
    with _locked(directory):
        plan = _load(path)
        if (
            plan.get("schema_version") != PLAN_SCHEMA
            or plan.get("url") != validate_url(client.url)
            or plan.get("operator_verdict") != "false_positive"
            or plan.get("future_use") != "exact_reuse"
        ):
            raise BulkReviewError(
                "Plan identity, origin or requested decision is invalid"
            )
        ids = [item.get("candidate_id") for item in plan.get("items", [])]
        if (
            len(ids) != plan.get("expected_count")
            or not ids
            or len(set(ids)) != len(ids)
        ):
            raise BulkReviewError("Plan candidate inventory is invalid")
        for item in plan["items"]:
            if (
                canonical_hash(item["candidate"]) != item["candidate_hash"]
                or item["candidate"].get("candidate_id") != item["candidate_id"]
            ):
                raise BulkReviewError("Plan candidate hash/identity is invalid")
            _scope_identity(item["candidate"])
            if (
                not item["blocked_reason"]
                and default_applicability(item["candidate"])
                != item["record_applicability"]
            ):
                raise BulkReviewError("Plan changes the default review scope")
        _idle_learning(client, plan["experiment_id"])
        state_path = directory / "state.json"
        state = (
            _load(state_path)
            if state_path.exists()
            else {"plan_hash": canonical_hash(plan), "items": {}}
        )
        if state.get("plan_hash") != canonical_hash(plan):
            raise BulkReviewError("Resume state belongs to a different plan")
        for index, item in enumerate(plan["items"], 1):
            cid = item["candidate_id"]
            saved = state["items"].setdefault(cid, {"status": "pending"})
            if saved["status"] in {"completed", "confirmed_unverified"}:
                continue
            if item["blocked_reason"]:
                saved.update(status="blocked", error=item["blocked_reason"])
                _receipts(directory, plan, state)
                continue
            if (
                saved["status"] in {"blocked", "draft_failed", "drafting"}
                and not retry_failed
            ):
                continue
            stage = "precheck"
            try:
                _idle_learning(client, plan["experiment_id"])
                # Resume only an unchanged pending candidate. A confirmed candidate
                # could belong to another operator; no read API proves our audit key.
                if "review_payload" not in saved:
                    candidate = _same_candidate(client, item)
                    if "draft" not in saved:
                        stage = "draft"
                        saved.update(status="drafting", error=None)
                        _receipts(directory, plan, state)
                        draft = client.post(
                            _candidate_path(cid) + "/lesson-draft",
                            {
                                "reviewer_verdict": "false_positive",
                                "reviewer_context": "操作人指定本次 DEV 实验第一批经验批量采用误报结论；这不是逐条人工复核记录，不得虚构业务授权或缺失证据。",
                                "promoted_facet_keys": [],
                                "promoted_facet_values": {},
                                "selected_behavior_components": item[
                                    "record_applicability"
                                ]["selected_behavior_components"],
                            },
                        )
                        validate_draft(cid, draft)
                        saved.update(draft=draft, status="draft_ready")
                        _receipts(directory, plan, state)
                    stage = "precheck"
                    _same_candidate(client, item)
                    payload = review_payload(
                        candidate,
                        item["record_applicability"],
                        saved["draft"],
                        plan["experiment_id"],
                    )
                    saved.update(
                        review_payload=payload,
                        idempotency_key="bulk-memory-"
                        + canonical_hash([canonical_hash(plan), cid, payload]),
                        status="review_ready",
                    )
                    _receipts(directory, plan, state)
                else:
                    _saved_review(plan, item, saved)
                    current = client.get(_candidate_path(cid))
                    if current.get("status") in PENDING:
                        if canonical_hash(current) != item["candidate_hash"]:
                            raise BulkReviewError(
                                "Candidate changed before saved review retry"
                            )
                    elif current.get("status") == "confirmed":
                        saved.update(
                            status="confirmed_unverified",
                            error="Candidate is confirmed but this request's commit cannot be proven; inspect its Memory record before any further action",
                        )
                        _receipts(directory, plan, state)
                        print(
                            f"[{index}/{len(ids)}] {cid}: confirmed_unverified",
                            flush=True,
                        )
                        continue
                    else:
                        raise BulkReviewError(
                            "Candidate is no longer pending; not reviewed"
                        )
                stage = "review"
                saved.update(status="reviewing", error=None)
                _receipts(directory, plan, state)
                result = client.post(
                    _candidate_path(cid) + "/review",
                    saved["review_payload"],
                    idempotency_key=saved["idempotency_key"],
                )
                saved["review_result"] = result
                memory_id = _verify_result(cid, result)
                saved.update(status="completed", memory_id=memory_id, error=None)
            except (ApiError, BulkReviewError) as exc:
                unknown = isinstance(exc, ApiError) and (
                    exc.status is None or exc.status >= 500
                )
                status = (
                    "draft_failed"
                    if stage == "draft"
                    else "review_unknown"
                    if stage == "review" and (unknown or not isinstance(exc, ApiError))
                    else "blocked"
                )
                saved.update(
                    status=status,
                    error=(exc.detail if isinstance(exc, ApiError) else str(exc)),
                    error_code=(exc.status if isinstance(exc, ApiError) else None),
                )
                _receipts(directory, plan, state)
                if isinstance(exc, ApiError) and exc.status in {401, 403}:
                    raise BulkReviewError(
                        "Authentication/authorization failed; batch stopped, see private receipts"
                    ) from None
            result = _receipts(directory, plan, state)
            print(f"[{index}/{len(ids)}] {cid}: {saved['status']}", flush=True)
        result = _receipts(directory, plan, state)
        print(
            "Review counts: " + json.dumps(result["counts"], sort_keys=True), flush=True
        )
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser(
        "plan", help="Read and freeze current DEV first-batch pending candidates"
    )
    plan.add_argument("--output-dir", required=True)
    plan.add_argument("--expected-count", required=True, type=int)
    plan.add_argument("--experiment")
    apply = commands.add_parser(
        "apply", help="Generate lessons and submit the operator's batch review"
    )
    apply.add_argument("--plan", required=True)
    apply.add_argument("--confirm", required=True, choices=["APPROVE-FALSE-POSITIVE"])
    apply.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly retry blocked/failed drafts; may make another model call",
    )
    for command in (plan, apply):
        command.add_argument("--url", default="http://127.0.0.1:2026")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        client = ApiClient(args.url)
        if args.command == "plan":
            build_plan(client, args.output_dir, args.expected_count, args.experiment)
        else:
            result = apply_plan(client, args.plan, args.retry_failed)
            return 0 if set(result["counts"]) <= {"completed"} else 2
    except (BulkReviewError, OSError, ValueError, KeyError, TypeError) as exc:
        print(
            f"Error: {exc if isinstance(exc, BulkReviewError) else type(exc).__name__}; no automatic retry",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
