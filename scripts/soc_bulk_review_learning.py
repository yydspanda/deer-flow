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
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE = "/api/soc/dev/corpus-workbench"
MEMORY = "/api/soc/memory/candidates"
PENDING = {"pending_review", "confirmed_candidate"}
PLAN_SCHEMA = "soc.dev_learning_bulk_review_plan.v2"
LEGACY_PLAN_SCHEMA = "soc.dev_learning_bulk_review_plan.v1"
INCREMENTAL_PLAN_SCHEMA = "soc.dev_learning_bulk_review_plan.v3"
REVIEW_POLICY = {"review_after_days": 180, "validity_days": 365}
# The synchronous endpoint may use a model call plus one output repair. Keep
# the client above the dedicated nginx route's 1800s response wait budget.
DRAFT_HTTP_TIMEOUT_SECONDS = 1810.0


class BulkReviewError(RuntimeError):
    """An operator-visible bounded error, never a raw HTTP body."""


class ReviewScopeChanged(BulkReviewError):
    """Stop further admissions when the frozen incremental scope is no longer idle."""


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
            # urllib handlers can keep request state; never share them across workers.
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect()
            )
            with opener.open(
                urllib.request.Request(
                    self.url + path, data=data, headers=headers, method=method
                ),
                timeout=(
                    max(self.timeout, DRAFT_HTTP_TIMEOUT_SECONDS)
                    if method == "POST"
                    and re.fullmatch(
                        r"/api/soc/memory/candidates/[^/]+/lesson-draft", path
                    )
                    else self.timeout
                ),
            ) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ApiError(None, "Response too large")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Save bounded server detail privately; never print credentials or response bodies.
            raw = exc.read(32_768)
            fallback = (
                "Gateway timeout (HTTP 504); inspect proxy/upstream timeout logs; "
                "request completion is unknown"
                if exc.code == 504
                else "Request rejected"
            )
            try:
                detail = str(json.loads(raw).get("detail", fallback))[:4000]
            except (ValueError, AttributeError):
                detail = fallback
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


def _idle_learning(
    client,
    expected_experiment=None,
    *,
    explicit=False,
    expected_plan_id=None,
    validation_started=False,
    expected_validation_count=None,
):
    if explicit:
        if not isinstance(expected_experiment, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,63}", expected_experiment
        ):
            raise BulkReviewError("Invalid explicit experiment identity")
        prefix = f"{BASE}/experiments/{expected_experiment}"
        details = client.get(prefix)
        plan_id = details.get("plan_id")
        if (
            details.get("experiment_id") != expected_experiment
            or details.get("tenant_id") != "pingan"
            or details.get("environment") != "dev-corpus-eval"
            or details.get("fact_source_experiment_id") is not None
            or not isinstance(plan_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", plan_id)
            or (expected_plan_id is not None and plan_id != expected_plan_id)
        ):
            raise BulkReviewError(
                "Explicit experiment does not match the DEV review scope"
            )
        learning = client.get(f"{prefix}/status?batch=learning")
        validation = client.get(f"{prefix}/status?batch=validation")
        if any(
            view.get("review_allowed") is not True
            or view.get("fact_source_experiment_id") is not None
            or view.get("plan_id") != plan_id
            for view in (learning, validation)
        ):
            raise BulkReviewError(
                "Fact preparation or changed experiments cannot be reviewed"
            )
        if (
            learning.get("batch") != "learning"
            or validation.get("batch") != "validation"
        ):
            raise BulkReviewError("Explicit experiment returned the wrong batch state")
    else:
        learning = client.get(f"{BASE}/quick-validation?batch=learning&limit=1")
        validation = client.get(f"{BASE}/quick-validation?batch=validation&limit=1")
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
    if (
        validation.get("experiment_id") != experiment
        or any(
            validation.get(key) != 0
            for key in (
                ("active",) if validation_started else ("completed", "failed", "active")
            )
        )
        or any(
            validation.get(key) is not False for key in ("running", "manual_pending")
        )
    ):
        raise BulkReviewError(
            "Pause and drain validation before incremental review"
            if validation_started
            else "This tool requires a second batch that has not started"
        )
    if not validation_started and validation.get("remaining") != validation.get(
        "total"
    ):
        raise BulkReviewError("Validation state is not untouched")
    if validation_started and validation.get("total") != expected_validation_count:
        raise BulkReviewError("Report must cover the complete validation batch")
    return learning


def _result_identity(rows):
    if not isinstance(rows, list) or not rows:
        raise BulkReviewError("The validation report has no saved members")
    result = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("alert_id"), str)
            or not row["alert_id"]
            or row.get("status") not in {"completed", "failed", "queued", "cancelled"}
            or (row["status"] == "completed" and not row.get("run_id"))
        ):
            raise BulkReviewError(
                "Validation report contains invalid or active members"
            )
        result.append(
            {
                key: row.get(key)
                for key in (
                    "alert_id",
                    "job_id",
                    "run_id",
                    "status",
                    "attempt_count",
                    "payload_hash",
                    "group_id",
                )
            }
        )
    if len({row["alert_id"] for row in result}) != len(result):
        raise BulkReviewError("Validation report contains duplicate members")
    return canonical_hash(sorted(result, key=lambda row: row["alert_id"]))


def _round_identity(round_):
    return canonical_hash(
        {
            key: round_.get(key)
            for key in (
                "round_id",
                "experiment_id",
                "selection",
                "purpose",
                "learning_generation",
                "config_hash",
                "options",
                "memory_mode",
                "memory_snapshot",
            )
        }
    )


def _incremental_report(report_dir):
    directory = Path(report_dir).expanduser().absolute()
    path = directory / "report.json"
    if path.is_symlink() or path.stat().st_size > 256 * 1024 * 1024:
        raise BulkReviewError("Invalid or oversized report.json")
    raw = path.read_bytes()
    report = json.loads(raw)
    try:
        manifest = _load(directory / "manifest.json")
    except FileNotFoundError as exc:
        raise BulkReviewError(
            "Missing manifest.json; export the fixed validation report first"
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    round_ = report.get("round") or {}
    selection = round_.get("selection") or {}
    if (
        report.get("schema_version") != "soc.corpus_round_report.v1"
        or not isinstance(round_.get("round_id"), str)
        or not re.fullmatch(r"ROUND-[A-Za-z0-9_-]+", round_["round_id"])
        or manifest.get("round_id") != round_["round_id"]
        or manifest.get("files", {}).get("report.json") != digest
        or selection.get("batch") != "validation"
        or selection.get("scope") != "all"
        or selection.get("alert_ids")
        or selection.get("group_ids")
        or selection.get("rule_codes")
        or round_.get("purpose") == "facts"
        or round_.get("state") == "running"
        or report.get("progress", {}).get("active_count") != 0
    ):
        raise BulkReviewError(
            "Use the verified full validation report exported before incremental review"
        )
    rows = report.get("rows")
    identity = _result_identity(rows)
    if len(rows) != report.get("progress", {}).get("selected_count") or not any(
        row["status"] == "completed" for row in rows
    ):
        raise BulkReviewError(
            "Validation report membership is incomplete or has no completed results"
        )
    return {
        "directory": str(directory),
        "report_sha256": digest,
        "round_id": round_["round_id"],
        "experiment_id": round_.get("experiment_id"),
        "round_identity": _round_identity(round_),
        "result_identity": identity,
        "selected_count": len(rows),
    }


def _incremental_guard(
    client, baseline, *, expected_plan_id=None, check_all_active=False
):
    experiment = baseline["experiment_id"]
    learning = _idle_learning(
        client,
        experiment,
        explicit=True,
        expected_plan_id=expected_plan_id,
        validation_started=True,
        expected_validation_count=baseline["selected_count"],
    )
    progress = client.get(f"{BASE}/rounds/{baseline['round_id']}")
    round_ = progress.get("round") or {}
    if (
        _round_identity(round_) != baseline["round_identity"]
        or round_.get("superseded_by_round_id")
        or round_.get("state") not in {"paused", "completed"}
        or progress.get("active_count") != 0
        or progress.get("selected_count") != baseline["selected_count"]
    ):
        raise BulkReviewError(
            "Validation source changed, is superseded, or has not paused and drained"
        )
    rounds, offset = [], 0
    while True:
        page = client.get(
            f"{BASE}/rounds?experiment_id={experiment}&limit=20&offset={offset}"
        )
        if not isinstance(page, list) or any(
            not isinstance(row, dict) or row.get("experiment_id") != experiment
            for row in page
        ):
            raise BulkReviewError("Invalid experiment round inventory")
        rounds.extend(page)
        if any(row.get("state") == "running" for row in page):
            raise BulkReviewError(
                "Pause all experiment rounds before incremental review"
            )
        if len(page) < 20:
            break
        offset += len(page)
        if offset > 100_000:
            raise BulkReviewError("Experiment round inventory is too large")
    learning_rounds = [
        row for row in rounds if (row.get("selection") or {}).get("batch") == "learning"
    ]
    if not learning_rounds:
        raise BulkReviewError("Current learning generation cannot be verified")
    latest = max(
        learning_rounds,
        key=lambda row: (
            datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
            row["round_id"],
        ),
    )
    if latest.get("learning_generation") != round_.get("learning_generation"):
        raise BulkReviewError(
            "Validation report belongs to a different learning generation"
        )
    inventory = canonical_hash(
        sorted(
            [
                {
                    key: row.get(key)
                    for key in (
                        "round_id",
                        "version",
                        "state",
                        "learning_generation",
                        "superseded_by_round_id",
                    )
                }
                for row in rounds
            ],
            key=lambda row: row["round_id"],
        )
    )
    if (
        baseline.get("round_inventory") is not None
        and baseline["round_inventory"] != inventory
    ):
        raise BulkReviewError(
            "Experiment rounds changed after planning; export and plan again"
        )
    if check_all_active:
        for row in rounds:
            other = (
                progress
                if row["round_id"] == round_["round_id"]
                else client.get(f"{BASE}/rounds/{row['round_id']}")
            )
            if (
                other.get("active_count") != 0
                or other.get("round", {}).get("state") == "running"
            ):
                raise BulkReviewError(
                    "Wait for active tasks in all experiment rounds before review"
                )
    # Frozen round state and job counts must stay unchanged. Reviewed Memory itself
    # may change as this plan proceeds; it is intentionally not a Runtime retry.
    fence = canonical_hash(
        {key: value for key, value in progress.items() if key != "timing"}
    )
    if baseline.get("round_fence") is not None and fence != baseline["round_fence"]:
        raise BulkReviewError(
            "Validation round changed after planning; export and plan again"
        )
    return learning, fence, inventory


def _verify_report_results(client, baseline):
    rows, offset = [], 0
    while True:
        page = client.get(
            f"{BASE}/rounds/{baseline['round_id']}/results?limit=100&offset={offset}"
        )
        items = page.get("items")
        if (
            page.get("total") != baseline["selected_count"]
            or not isinstance(items, list)
            or not items
        ):
            raise BulkReviewError(
                "Validation results changed or pagination is incomplete"
            )
        rows.extend(items)
        offset += len(items)
        if offset >= page["total"]:
            break
    if (
        len(rows) != baseline["selected_count"]
        or _result_identity(rows) != baseline["result_identity"]
    ):
        raise BulkReviewError(
            "Validation was retried after export; export the report again"
        )


def _inventory(client, experiment, *, review_stage="pending"):
    entries, total = [], None
    while total is None or len(entries) < total:
        page = client.get(
            f"{BASE}/experiments/{experiment}/candidates?review_stage={review_stage}&limit=100&offset={len(entries)}"
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


def build_plan(
    client,
    output_dir,
    expected_count,
    experiment_id=None,
    *,
    phase="initial",
    report_dir=None,
):
    if not isinstance(expected_count, int) or expected_count <= 0:
        raise BulkReviewError("Expected candidate count must be positive")
    url = validate_url(client.url)
    if phase not in {"initial", "incremental"} or (
        (phase == "incremental") != (report_dir is not None)
    ):
        raise BulkReviewError(
            "Incremental review requires --report-dir; initial review must not use it"
        )
    baseline = None
    if phase == "incremental":
        baseline = _incremental_report(report_dir)
        if experiment_id is not None and experiment_id != baseline["experiment_id"]:
            raise BulkReviewError("Report experiment does not match --experiment")
        experiment_id = baseline["experiment_id"]
    explicit = experiment_id is not None
    if baseline:
        learning, baseline["round_fence"], baseline["round_inventory"] = (
            _incremental_guard(client, baseline, check_all_active=True)
        )
        _verify_report_results(client, baseline)
    else:
        learning = _idle_learning(client, experiment_id, explicit=explicit)
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
    if baseline:
        _incremental_guard(client, baseline, expected_plan_id=learning["plan_id"])
    else:
        _idle_learning(
            client,
            experiment,
            explicit=explicit,
            expected_plan_id=learning.get("plan_id") if explicit else None,
        )
    directory = Path(output_dir).expanduser().absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    plan = {
        "schema_version": INCREMENTAL_PLAN_SCHEMA if baseline else PLAN_SCHEMA,
        "url": url,
        "experiment_id": experiment,
        "created_at": datetime.now(UTC).isoformat(),
        "expected_count": expected_count,
        "operator_verdict": "false_positive",
        "future_use": "exact_reuse",
        "review_policy": dict(REVIEW_POLICY),
        "items": items,
        **(
            {"review_phase": "incremental", "baseline_report": baseline}
            if baseline
            else {}
        ),
        **(
            {
                "experiment_selection": "explicit",
                "experiment_plan_id": learning["plan_id"],
            }
            if explicit
            else {}
        ),
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


def review_payload(
    candidate,
    applicability,
    draft,
    experiment_id,
    now=None,
    *,
    review_after_days=180,
    validity_days=365,
    review_phase="initial",
    source_round_id=None,
):
    lesson = validate_draft(candidate["candidate_id"], draft)
    now = now or datetime.now(UTC)
    expires = now + timedelta(days=validity_days)
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
    if expires <= now + timedelta(days=review_after_days):
        raise BulkReviewError(
            "Candidate validity cannot cover the default review window"
        )
    return {
        "decision": "confirm",
        "reason": (
            "审核人按本次 DEV 增量批量审核指令统一选择误报，精确匹配时复用结论；保留页面默认的核心行为勾选条件。"
            if review_phase == "incremental"
            else "审核人按本次 DEV 第一批批量审核指令统一选择误报，精确匹配时复用结论；保留页面默认的核心行为勾选条件。"
        ),
        "record_lesson": lesson,
        "record_applicability": applicability,
        "confirmed_verdict": "false_positive",
        "apply_to_future_matches": True,
        "clear_review_on_match": True,
        "activate_retrieval": True,
        "activation_valid_until": expires.isoformat(),
        "activation_review_after_days": review_after_days,
        "metadata": {
            "source": "operator_bulk_review",
            "experiment_id": experiment_id,
            "batch": "validation" if review_phase == "incremental" else "learning",
            "individual_manual_review_claimed": False,
            **(
                {"review_phase": "incremental", "source_round_id": source_round_id}
                if review_phase == "incremental"
                else {}
            ),
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
        "execution": copy.deepcopy(state.get("execution", {})),
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


def _plan_review_policy(plan):
    """Schema changes never reinterpret a previously approved plan or request."""
    if plan.get("schema_version") == LEGACY_PLAN_SCHEMA:
        if "review_policy" not in plan:
            return {"review_after_days": 30, "validity_days": 60}
    elif plan.get("schema_version") in {PLAN_SCHEMA, INCREMENTAL_PLAN_SCHEMA}:
        policy = plan.get("review_policy")
        if policy == REVIEW_POLICY and all(
            type(value) is int for value in policy.values()
        ):
            return dict(REVIEW_POLICY)
    raise BulkReviewError("Plan review policy is invalid for its schema")


def _saved_review(plan, item, saved):
    """Retain the exact immutable command across uncertain HTTP outcomes."""
    payload = saved["review_payload"]
    fixed = {
        "decision": "confirm",
        "confirmed_verdict": "false_positive",
        "apply_to_future_matches": True,
        "clear_review_on_match": True,
        "activate_retrieval": True,
        "activation_review_after_days": _plan_review_policy(plan)["review_after_days"],
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


def apply_plan(client, plan_path, retry_failed=False, concurrency=8):
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise BulkReviewError("Review concurrency must be an integer from 1 to 8")
    path = Path(plan_path).expanduser().absolute()
    directory = path.parent
    with _locked(directory):
        plan = _load(path)
        if (
            plan.get("schema_version")
            not in {LEGACY_PLAN_SCHEMA, PLAN_SCHEMA, INCREMENTAL_PLAN_SCHEMA}
            or plan.get("url") != validate_url(client.url)
            or plan.get("operator_verdict") != "false_positive"
            or plan.get("future_use") != "exact_reuse"
        ):
            raise BulkReviewError(
                "Plan identity, origin or requested decision is invalid"
            )
        review_policy = _plan_review_policy(plan)
        incremental = plan["schema_version"] == INCREMENTAL_PLAN_SCHEMA
        if incremental:
            baseline = plan.get("baseline_report")
            if (
                plan.get("review_phase") != "incremental"
                or not isinstance(baseline, dict)
                or baseline.get("experiment_id") != plan["experiment_id"]
            ):
                raise BulkReviewError("Incremental plan provenance is invalid")
            source = _incremental_report(baseline["directory"])
            if any(
                baseline.get(key) != value for key, value in source.items()
            ) or not all(
                isinstance(baseline.get(key), str)
                and re.fullmatch(r"[0-9a-f]{64}", baseline[key])
                for key in ("round_fence", "round_inventory")
            ):
                raise BulkReviewError("Saved baseline report changed after planning")
        elif (
            plan.get("review_phase", "initial") != "initial"
            or "baseline_report" in plan
        ):
            raise BulkReviewError("Legacy plans cannot enable incremental review")
        selection = plan.get("experiment_selection", "quick")
        if selection not in {"quick", "explicit"}:
            raise BulkReviewError("Unknown experiment selection mode")
        explicit = selection == "explicit"
        if incremental and not explicit:
            raise BulkReviewError("Incremental plans must bind an explicit experiment")
        if explicit and (
            not isinstance(plan.get("experiment_plan_id"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", plan["experiment_plan_id"])
        ):
            raise BulkReviewError("Explicit experiment plan identity is invalid")
        scope_options = {
            "explicit": explicit,
            "expected_plan_id": plan.get("experiment_plan_id") if explicit else None,
        }

        def guard(*, check_all_active=False):
            if incremental:
                try:
                    _incremental_guard(
                        client,
                        baseline,
                        expected_plan_id=plan["experiment_plan_id"],
                        check_all_active=check_all_active,
                    )
                except ApiError:
                    raise
                except BulkReviewError as exc:
                    raise ReviewScopeChanged(str(exc)) from exc
            else:
                _idle_learning(client, plan["experiment_id"], **scope_options)

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
        guard(check_all_active=True)
        if explicit and not set(ids).issubset(
            _inventory(client, plan["experiment_id"], review_stage="all")
        ):
            raise BulkReviewError("Plan candidates are outside the selected experiment")
        state_path = directory / "state.json"
        state = (
            _load(state_path)
            if state_path.exists()
            else {"plan_hash": canonical_hash(plan), "items": {}}
        )
        if state.get("plan_hash") != canonical_hash(plan):
            raise BulkReviewError("Resume state belongs to a different plan")
        state["execution"] = {
            "concurrency": concurrency,
            "started_at": datetime.now(UTC).isoformat(),
        }
        state_lock = threading.RLock()
        stop = threading.Event()
        fatal = []

        def checkpoint(cid, saved):
            with state_lock:
                state["items"][cid] = copy.deepcopy(saved)
                return _receipts(directory, plan, state)

        class AdmissionStopped(Exception):
            pass

        def post(cid, saved, phase, path, payload, idempotency_key=None):
            # The gate defines admission. Requests admitted before a fatal event
            # are in flight and must finish recording their actual outcome.
            with state_lock:
                if stop.is_set():
                    raise AdmissionStopped
                saved.update(status=phase, error=None)
                checkpoint(cid, saved)
            return client.post(path, payload, idempotency_key=idempotency_key)

        def process_item(index, item):
            if stop.is_set():
                return
            cid = item["candidate_id"]
            with state_lock:
                saved = copy.deepcopy(state["items"].get(cid, {"status": "pending"}))
            if saved["status"] in {"completed", "confirmed_unverified"}:
                return
            if item["blocked_reason"]:
                saved.update(status="blocked", error=item["blocked_reason"])
                checkpoint(cid, saved)
                return
            if (
                saved["status"] in {"blocked", "draft_failed", "drafting"}
                and not retry_failed
            ):
                return
            stage = "precheck"
            try:
                guard()
                # Resume only an unchanged pending candidate. A confirmed candidate
                # could belong to another operator; no read API proves our audit key.
                if "review_payload" not in saved:
                    candidate = _same_candidate(client, item)
                    if "draft" not in saved:
                        stage = "draft"
                        draft = post(
                            cid,
                            saved,
                            "drafting",
                            _candidate_path(cid) + "/lesson-draft",
                            {
                                "reviewer_verdict": "false_positive",
                                "reviewer_context": (
                                    "操作人指定本次 DEV 实验新增待审经验批量采用误报结论；这不是逐条人工复核记录，不得虚构业务授权或缺失证据。"
                                    if incremental
                                    else "操作人指定本次 DEV 实验第一批经验批量采用误报结论；这不是逐条人工复核记录，不得虚构业务授权或缺失证据。"
                                ),
                                "promoted_facet_keys": [],
                                "promoted_facet_values": {},
                                "selected_behavior_components": item[
                                    "record_applicability"
                                ]["selected_behavior_components"],
                            },
                        )
                        validate_draft(cid, draft)
                        saved.update(draft=draft, status="draft_ready")
                        checkpoint(cid, saved)
                    if stop.is_set():
                        return
                    stage = "precheck"
                    _same_candidate(client, item)
                    payload = review_payload(
                        candidate,
                        item["record_applicability"],
                        saved["draft"],
                        plan["experiment_id"],
                        **review_policy,
                        **(
                            {
                                "review_phase": "incremental",
                                "source_round_id": baseline["round_id"],
                            }
                            if incremental
                            else {}
                        ),
                    )
                    saved.update(
                        review_payload=payload,
                        idempotency_key="bulk-memory-"
                        + canonical_hash([canonical_hash(plan), cid, payload]),
                        status="review_ready",
                    )
                    checkpoint(cid, saved)
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
                        checkpoint(cid, saved)
                        print(
                            f"[{index}/{len(ids)}] {cid}: confirmed_unverified",
                            flush=True,
                        )
                        return
                    else:
                        raise BulkReviewError(
                            "Candidate is no longer pending; not reviewed"
                        )
                if incremental:
                    guard()
                stage = "review"
                result = post(
                    cid,
                    saved,
                    "reviewing",
                    _candidate_path(cid) + "/review",
                    saved["review_payload"],
                    idempotency_key=saved["idempotency_key"],
                )
                saved["review_result"] = result
                memory_id = _verify_result(cid, result)
                saved.update(status="completed", memory_id=memory_id, error=None)
            except AdmissionStopped:
                return
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
                if isinstance(exc, ReviewScopeChanged) or (
                    isinstance(exc, ApiError) and exc.status in {401, 403}
                ):
                    with state_lock:
                        stop.set()
                        if not fatal:
                            fatal.append(
                                BulkReviewError(
                                    str(exc)
                                    if isinstance(exc, ReviewScopeChanged)
                                    else "Authentication/authorization failed; batch stopped, see private receipts"
                                )
                            )
                            print(
                                (
                                    "Review scope changed"
                                    if isinstance(exc, ReviewScopeChanged)
                                    else "Authorization failed"
                                )
                                + "; stopping new admissions and saving in-flight outcomes.",
                                flush=True,
                            )
                        checkpoint(cid, saved)
            except BaseException:
                # Preserve drafting/reviewing for explicit safe recovery if a worker
                # is interrupted after dispatch; never claim that it did not commit.
                with state_lock:
                    stop.set()
                    checkpoint(cid, saved)
                raise
            checkpoint(cid, saved)
            with state_lock:
                print(f"[{index}/{len(ids)}] {cid}: {saved['status']}", flush=True)

        with state_lock:
            _receipts(directory, plan, state)
        print(
            f"Review concurrency: {concurrency} (maximum in-flight candidates)",
            flush=True,
        )
        items = iter(enumerate(plan["items"], 1))
        active = set()
        executor = ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="soc-review"
        )
        interrupted = None
        try:
            while True:
                while not stop.is_set() and len(active) < concurrency:
                    next_item = next(items, None)
                    if next_item is None:
                        break
                    active.add(executor.submit(process_item, *next_item))
                if not active:
                    break
                done, active = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
        except BaseException as exc:
            with state_lock:
                stop.set()
            interrupted = exc
            print(
                "Stopping new admissions; waiting for in-flight requests and preserving receipts.",
                flush=True,
            )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        with state_lock:
            result = _receipts(directory, plan, state)
        print(
            "Review counts: " + json.dumps(result["counts"], sort_keys=True), flush=True
        )
        if interrupted is not None:
            raise interrupted
        if fatal:
            raise fatal[0]
        return result


def review_status(plan_path):
    """Read atomic local receipts, without locking a running apply or using HTTP."""
    path = Path(plan_path).expanduser().absolute()
    plan = _load(path)
    if plan.get("schema_version") not in {
        LEGACY_PLAN_SCHEMA,
        PLAN_SCHEMA,
        INCREMENTAL_PLAN_SCHEMA,
    }:
        raise BulkReviewError("Unknown review plan schema")
    state_path = path.parent / "state.json"
    state = (
        _load(state_path)
        if state_path.exists()
        else {"plan_hash": canonical_hash(plan), "items": {}}
    )
    if state.get("plan_hash") != canonical_hash(plan):
        raise BulkReviewError("Resume state belongs to a different plan")
    counts = Counter(
        state["items"].get(item["candidate_id"], {}).get("status")
        or ("blocked" if item["blocked_reason"] else "pending")
        for item in plan["items"]
    )
    result = {
        "experiment_id": plan["experiment_id"],
        "review_phase": plan.get("review_phase", "initial"),
        "source_round_id": (plan.get("baseline_report") or {}).get("round_id"),
        "candidate_count": len(plan["items"]),
        "counts": dict(counts),
        "concurrency": state.get("execution", {}).get("concurrency"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser(
        "plan",
        help="Read and freeze DEV pending candidates for initial or incremental review",
    )
    plan.add_argument("--output-dir", required=True)
    plan.add_argument("--expected-count", required=True, type=int)
    plan.add_argument("--phase", choices=("initial", "incremental"), default="initial")
    plan.add_argument(
        "--report-dir",
        help="Incremental mode: fixed validation export with report.json and manifest.json",
    )
    plan.add_argument(
        "--experiment",
        help="Review this existing DEV experiment, including a rebalanced experiment; defaults to quick validation",
    )
    apply = commands.add_parser(
        "apply", help="Generate lessons and submit the operator's batch review"
    )
    apply.add_argument("--plan", required=True)
    apply.add_argument("--confirm", required=True, choices=["APPROVE-FALSE-POSITIVE"])
    apply.add_argument(
        "--concurrency",
        type=int,
        choices=range(1, 9),
        default=8,
        help="Maximum simultaneous candidate pipelines (default: 8; use 1 for serial)",
    )
    apply.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly retry failed or uncertain drafts; applicability blocks remain",
    )
    status = commands.add_parser(
        "status", help="Read local plan progress without calling the API"
    )
    status.add_argument("--plan", required=True)
    for command in (plan, apply):
        command.add_argument("--url", default="http://127.0.0.1:2026")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command == "status":
            review_status(args.plan)
            return 0
        client = ApiClient(args.url)
        if args.command == "plan":
            build_plan(
                client,
                args.output_dir,
                args.expected_count,
                args.experiment,
                phase=args.phase,
                report_dir=args.report_dir,
            )
        else:
            result = apply_plan(client, args.plan, args.retry_failed, args.concurrency)
            return 0 if set(result["counts"]) <= {"completed"} else 2
    except KeyboardInterrupt:
        print(
            "Interrupted; no new candidates started. Saved receipts retain completed and uncertain outcomes.",
            file=sys.stderr,
        )
        return 130
    except (BulkReviewError, OSError, ValueError, KeyError, TypeError) as exc:
        print(
            f"Error: {exc if isinstance(exc, BulkReviewError) else type(exc).__name__}; no automatic retry",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
