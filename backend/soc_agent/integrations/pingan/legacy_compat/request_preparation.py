"""Prepare a private legacy ZEUS request from the staged corpus payload store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.integrations.pingan.legacy_compat.contracts import (
    PingAnLegacyTaskRequest,
)
from soc_agent.utils.hashing import stable_hash

_INDEX_SCHEMA_VERSION = "soc.corpus_workbench_index.v3"
_PAYLOAD_STORE_SCHEMA_VERSION = "soc.corpus_workbench_payload_store.v1"
_PENDING_REVIEW_STATUSES = frozenset({"1", "pending", "pending_review", "待审阅"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024


class PingAnLegacyRequestPreparationError(ValueError):
    """Raised when a safe live-acceptance request cannot be prepared."""


class PingAnLegacyRequestPreparationReport(BaseModel):
    """Business-payload-free evidence that a private request was prepared."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_legacy_request_preparation.v1"] = "soc.pingan_legacy_request_preparation.v1"
    output_path: str
    alert_id: str
    session_id: str
    app_code: Literal["zeus"] = "zeus"
    flow_id: Literal["alert_agent"] = "alert_agent"
    source_kind: Literal["corpus_workbench_payload_store"] = "corpus_workbench_payload_store"
    source_index: int = Field(ge=0)
    snapshot_status: str
    payload_sha256: str = Field(min_length=64, max_length=64)
    payload_size_bytes: int = Field(gt=0)
    contains_business_payload: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def prepare_pingan_legacy_live_request(
    *,
    alert_id: str,
    index_path: Path,
    output_path: Path,
    session_id: str | None = None,
    overwrite: bool = False,
    max_payload_bytes: int = _DEFAULT_MAX_PAYLOAD_BYTES,
) -> PingAnLegacyRequestPreparationReport:
    """Build one complete old-ZEUS request without manual JSON editing."""

    normalized_alert_id = alert_id.strip()
    if not normalized_alert_id:
        raise PingAnLegacyRequestPreparationError("alert_id is required")
    if max_payload_bytes <= 0:
        raise PingAnLegacyRequestPreparationError("max_payload_bytes must be positive")

    resolved_index = index_path.expanduser().resolve()
    index = _load_index(resolved_index)
    case = _find_case(index, normalized_alert_id)
    store_path = _validate_payload_store(index, resolved_index)
    payload, source_index, payload_size = _load_payload(
        store_path,
        case=case,
        alert_id=normalized_alert_id,
        max_payload_bytes=max_payload_bytes,
    )
    snapshot_status = _validate_alert_snapshot(
        payload,
        expected_alert_id=normalized_alert_id,
    )

    normalized_session_id = (session_id or f"soc-live-{uuid4().hex}").strip()
    request = PingAnLegacyTaskRequest.model_validate(
        {
            "app_code": "zeus",
            "flow_id": "alert_agent",
            "session_id": normalized_session_id,
            "alert_id": normalized_alert_id,
            "alert_data": payload,
        }
    )
    resolved_output = _private_output_path(output_path, overwrite=overwrite)
    _write_private_json(
        resolved_output,
        request.model_dump(mode="json"),
    )
    return PingAnLegacyRequestPreparationReport(
        output_path=str(resolved_output),
        alert_id=normalized_alert_id,
        session_id=normalized_session_id,
        source_index=source_index,
        snapshot_status=snapshot_status,
        payload_sha256=stable_hash(payload),
        payload_size_bytes=payload_size,
    )


def _load_index(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PingAnLegacyRequestPreparationError(f"corpus workbench index is missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PingAnLegacyRequestPreparationError(f"corpus workbench index is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise PingAnLegacyRequestPreparationError("corpus workbench index must be a JSON object")
    if document.get("schema_version") != _INDEX_SCHEMA_VERSION:
        raise PingAnLegacyRequestPreparationError("unsupported corpus workbench index schema; rebuild the staged corpus")
    return document


def _find_case(index: Mapping[str, Any], alert_id: str) -> Mapping[str, Any]:
    cases = index.get("cases")
    if not isinstance(cases, list):
        raise PingAnLegacyRequestPreparationError("corpus workbench index has no case catalog")
    matches = [item for item in cases if isinstance(item, Mapping) and str(item.get("alert_id", "")).strip() == alert_id]
    if not matches:
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} is not present in the staged corpus")
    if len(matches) != 1:
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} is duplicated in the corpus workbench index")
    return matches[0]


def _validate_payload_store(
    index: Mapping[str, Any],
    index_path: Path,
) -> Path:
    source = index.get("source")
    store = index.get("payload_store")
    if not isinstance(source, Mapping) or not isinstance(store, Mapping):
        raise PingAnLegacyRequestPreparationError("corpus workbench index is missing source or payload-store identity")
    source_sha256 = _sha256_value(source.get("sha256"), "source sha256")
    if store.get("schema_version") != _PAYLOAD_STORE_SCHEMA_VERSION:
        raise PingAnLegacyRequestPreparationError("unsupported corpus payload-store schema; rebuild the staged corpus")
    file_name = str(store.get("file_name", "")).strip()
    if not file_name or Path(file_name).name != file_name:
        raise PingAnLegacyRequestPreparationError("corpus payload-store file name is invalid")
    expected_size = _positive_int(store.get("size_bytes"), "payload-store size")
    expected_sha256 = _sha256_value(store.get("sha256"), "payload-store sha256")
    path = (index_path.parent / file_name).resolve()
    if path.parent != index_path.parent.resolve() or not path.is_file():
        raise PingAnLegacyRequestPreparationError(f"corpus payload store is missing: {path}")
    if path.stat().st_size != expected_size:
        raise PingAnLegacyRequestPreparationError("corpus payload-store size does not match the workbench index")
    if _sha256_file(path) != expected_sha256:
        raise PingAnLegacyRequestPreparationError("corpus payload-store hash does not match the workbench index")

    connection = _open_read_only_store(path)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    except sqlite3.Error as exc:
        raise PingAnLegacyRequestPreparationError(f"corpus payload-store metadata is invalid: {exc}") from exc
    finally:
        connection.close()
    if metadata.get("schema_version") != _PAYLOAD_STORE_SCHEMA_VERSION:
        raise PingAnLegacyRequestPreparationError("corpus payload-store metadata version does not match the contract")
    if metadata.get("source_sha256") != source_sha256:
        raise PingAnLegacyRequestPreparationError("corpus payload store and workbench index refer to different source data")
    expected_count = _positive_int(source.get("alert_count"), "source alert count")
    if _positive_int(metadata.get("alert_count"), "payload-store alert count") != expected_count:
        raise PingAnLegacyRequestPreparationError("corpus payload-store alert count does not match the workbench index")
    return path


def _load_payload(
    path: Path,
    *,
    case: Mapping[str, Any],
    alert_id: str,
    max_payload_bytes: int,
) -> tuple[dict[str, Any], int, int]:
    expected_source_index = _non_negative_int(
        case.get("source_index"),
        "case source index",
    )
    expected_payload_hash = _sha256_value(
        case.get("payload_hash"),
        "case payload hash",
    )
    connection = _open_read_only_store(path)
    try:
        row = connection.execute(
            """
            SELECT source_index, payload_hash, raw_size, payload_zlib
            FROM payloads
            WHERE alert_id = ?
            """,
            (alert_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise PingAnLegacyRequestPreparationError(f"failed to read alert {alert_id} from the corpus payload store: {exc}") from exc
    finally:
        connection.close()
    if row is None:
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} is missing from the corpus payload store")

    source_index, payload_hash, raw_size, compressed = row
    if _non_negative_int(source_index, "stored source index") != expected_source_index:
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} source identity does not match the workbench index")
    if str(payload_hash) != expected_payload_hash:
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} payload identity does not match the workbench index")
    payload_size = _positive_int(raw_size, "stored payload size")
    if payload_size > max_payload_bytes:
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} payload exceeds the {max_payload_bytes}-byte safety limit")
    try:
        encoded = zlib.decompress(compressed)
        payload = json.loads(encoded)
    except (TypeError, zlib.error, json.JSONDecodeError) as exc:
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} payload-store content is invalid: {exc}") from exc
    if len(encoded) != payload_size or not isinstance(payload, dict):
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} payload-store content failed validation")
    if stable_hash(payload) != expected_payload_hash:
        raise PingAnLegacyRequestPreparationError(f"alert_id {alert_id} payload hash does not match the workbench index")
    return payload, expected_source_index, payload_size


def _validate_alert_snapshot(
    payload: Mapping[str, Any],
    *,
    expected_alert_id: str,
) -> str:
    alert = payload.get("alert")
    if not isinstance(alert, Mapping):
        raise PingAnLegacyRequestPreparationError("staged payload does not contain the complete alert object")
    nested_alert_id = _first_text(alert, "alertId", "alert_id", "id")
    if nested_alert_id != expected_alert_id:
        raise PingAnLegacyRequestPreparationError("staged payload nested alert ID does not match the selected alert_id")
    status = _first_text(alert, "status")
    if status is None or status.casefold() not in _PENDING_REVIEW_STATUSES:
        rendered = status or "missing"
        raise PingAnLegacyRequestPreparationError(f"alert_id {expected_alert_id} snapshot is not pending review (status={rendered})")
    return status


def _private_output_path(path: Path, *, overwrite: bool) -> Path:
    candidate = path.expanduser()
    if not candidate.name.endswith(".local.json"):
        raise PingAnLegacyRequestPreparationError("private request output must use the .local.json suffix")
    if candidate.is_symlink():
        raise PingAnLegacyRequestPreparationError("private request output must not be a symbolic link")
    resolved = candidate.resolve()
    if resolved.exists() and not overwrite:
        raise PingAnLegacyRequestPreparationError(f"private request output already exists: {resolved}; pass --overwrite to replace it")
    if resolved.exists() and not resolved.is_file():
        raise PingAnLegacyRequestPreparationError(f"private request output is not a regular file: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except OSError as exc:
        raise PingAnLegacyRequestPreparationError(f"failed to write private request file: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _open_read_only_store(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise PingAnLegacyRequestPreparationError(f"cannot open corpus payload store read-only: {exc}") from exc


def _first_text(values: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def _sha256_value(value: Any, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA256_PATTERN.fullmatch(normalized) is None:
        raise PingAnLegacyRequestPreparationError(f"{name} is invalid")
    return normalized


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PingAnLegacyRequestPreparationError(f"{name} is invalid") from exc
    if parsed <= 0:
        raise PingAnLegacyRequestPreparationError(f"{name} must be positive")
    return parsed


def _non_negative_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PingAnLegacyRequestPreparationError(f"{name} is invalid") from exc
    if parsed < 0:
        raise PingAnLegacyRequestPreparationError(f"{name} must be non-negative")
    return parsed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "PingAnLegacyRequestPreparationError",
    "PingAnLegacyRequestPreparationReport",
    "prepare_pingan_legacy_live_request",
]
