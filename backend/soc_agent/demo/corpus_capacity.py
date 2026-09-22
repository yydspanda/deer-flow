"""Host-owned corpus admission capacity, separate from analysis configuration."""

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

_SCHEMA_VERSION = "soc.corpus_capacity.v1"
_MAX_CONCURRENCY = 8


class CorpusCapacity:
    """Serialize limit changes with short claims, never with model execution.

    One DEV Gateway owns this setting. Saving it uses an atomic file replacement,
    so adjusting throughput does not need another SQLite write transaction.
    """

    def __init__(self, ceiling: int, path: Path | None = None) -> None:
        if type(ceiling) is not int or ceiling < 1:
            raise ValueError("Corpus capacity ceiling must be a positive integer")
        self._ceiling = min(_MAX_CONCURRENCY, ceiling)
        self._path = path
        self._lock = RLock()
        self._max_concurrency = self._load_limit()

    @property
    def ceiling(self) -> int:
        return self._ceiling

    @property
    def max_concurrency(self) -> int:
        with self._lock:
            return self._max_concurrency

    @contextmanager
    def admission(self) -> Iterator[int]:
        """Hold through the short durable claim; release before model work."""
        with self._lock:
            yield self._max_concurrency

    def set_limit(self, value: int, *, actor_id: str) -> None:
        if type(value) is not int or not 1 <= value <= self._ceiling:
            raise ValueError(f"Corpus capacity must be an integer between 1 and {self._ceiling}")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("Corpus capacity changes require an actor_id")
        with self._lock:
            if self._path is not None:
                self._persist(value, actor_id=actor_id)
            self._max_concurrency = value

    def _load_limit(self) -> int:
        if self._path is None:
            return self._ceiling
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._ceiling
        except UnicodeError as exc:
            raise ValueError(f"Invalid corpus capacity file {self._path}: expected UTF-8 JSON") from exc
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            value = payload.get("max_concurrency")
            if type(value) is not int or not 1 <= value <= _MAX_CONCURRENCY:
                raise ValueError("max_concurrency must be an integer between 1 and 8")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid corpus capacity file {self._path}: {exc}") from exc
        return min(value, self._ceiling)

    def _persist(self, value: int, *, actor_id: str) -> None:
        assert self._path is not None
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "max_concurrency": value,
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": actor_id,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
        finally:
            temporary_path.unlink(missing_ok=True)
