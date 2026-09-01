#!/usr/bin/env python3
"""Validate and stage separately transferred PingAn DEV corpus files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = Path("validation/compact_zeus/data/corpus")
CANONICAL_MANIFEST = CORPUS_DIR / "full_alert_validation_corpus.manifest.json"
WORKBENCH_INDEX = CORPUS_DIR / "full_alert_dams_labeled_merged.workbench-index.json"
RAW_TARGET = Path("datas/source/full_alert_2026_month_forth_sample_200.pkl")
CANONICAL_TARGET = CORPUS_DIR / "full_alert_validation_corpus.pkl"
MERGED_TARGET = CORPUS_DIR / "full_alert_dams_labeled_merged.pkl"
PAYLOAD_STORE_TARGET = (
    CORPUS_DIR / "full_alert_dams_labeled_merged.workbench-payloads.sqlite"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CorpusArtifact:
    name: str
    source_path: Path
    target_path: Path
    expected_sha256: str
    expected_size_bytes: int | None = None


def stage_internal_corpus(
    *,
    repo_root: Path = ROOT,
    downloads_root: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Verify all external files before atomically replacing any repo target."""

    repo_root = repo_root.expanduser().resolve()
    downloads_root = (
        (downloads_root or Path.home() / "Downloads").expanduser().resolve()
    )
    artifacts = _load_artifacts(repo_root=repo_root, downloads_root=downloads_root)
    missing = [
        str(item.source_path) for item in artifacts if not item.source_path.is_file()
    ]
    if missing:
        raise ValueError(f"external corpus files are missing: {missing}")

    reports = [_verify_source(item, repo_root=repo_root) for item in artifacts]
    if apply:
        for artifact, report in zip(artifacts, reports, strict=True):
            _copy_verified(artifact)
            report["target_verified"] = True

    return {
        "schema_version": "soc.pingan_internal_corpus_stage.v1",
        "ready": True,
        "applied": apply,
        "downloads_root": str(downloads_root),
        "repo_root": str(repo_root),
        "files": reports,
    }


def _load_artifacts(*, repo_root: Path, downloads_root: Path) -> list[CorpusArtifact]:
    canonical = _load_json_object(repo_root / CANONICAL_MANIFEST)
    source = _require_object(canonical, "source", CANONICAL_MANIFEST)
    output = _require_object(canonical, "output", CANONICAL_MANIFEST)
    _require_exact_path(source, "path", RAW_TARGET, CANONICAL_MANIFEST)
    _require_exact_path(output, "path", CANONICAL_TARGET, CANONICAL_MANIFEST)

    workbench = _load_json_object(repo_root / WORKBENCH_INDEX)
    merged = _require_object(workbench, "source", WORKBENCH_INDEX)
    payload_store = _require_object(workbench, "payload_store", WORKBENCH_INDEX)
    _require_exact_name(merged, "file_name", MERGED_TARGET.name, WORKBENCH_INDEX)
    _require_exact_name(
        payload_store,
        "file_name",
        PAYLOAD_STORE_TARGET.name,
        WORKBENCH_INDEX,
    )

    return [
        CorpusArtifact(
            name=RAW_TARGET.name,
            source_path=downloads_root / "source" / RAW_TARGET.name,
            target_path=repo_root / RAW_TARGET,
            expected_sha256=_require_sha256(source, CANONICAL_MANIFEST),
        ),
        CorpusArtifact(
            name=CANONICAL_TARGET.name,
            source_path=downloads_root / "corpus" / CANONICAL_TARGET.name,
            target_path=repo_root / CANONICAL_TARGET,
            expected_sha256=_require_sha256(output, CANONICAL_MANIFEST),
        ),
        CorpusArtifact(
            name=MERGED_TARGET.name,
            source_path=downloads_root / "corpus" / MERGED_TARGET.name,
            target_path=repo_root / MERGED_TARGET,
            expected_sha256=_require_sha256(merged, WORKBENCH_INDEX),
            expected_size_bytes=_require_size(merged, WORKBENCH_INDEX),
        ),
        CorpusArtifact(
            name=PAYLOAD_STORE_TARGET.name,
            source_path=downloads_root / "corpus" / PAYLOAD_STORE_TARGET.name,
            target_path=repo_root / PAYLOAD_STORE_TARGET,
            expected_sha256=_require_sha256(payload_store, WORKBENCH_INDEX),
            expected_size_bytes=_require_size(payload_store, WORKBENCH_INDEX),
        ),
    ]


def _verify_source(artifact: CorpusArtifact, *, repo_root: Path) -> dict[str, Any]:
    actual_size = artifact.source_path.stat().st_size
    if (
        artifact.expected_size_bytes is not None
        and actual_size != artifact.expected_size_bytes
    ):
        raise ValueError(
            f"size mismatch for {artifact.source_path}: expected "
            f"{artifact.expected_size_bytes}, found {actual_size}"
        )
    actual_sha256 = _sha256_file(artifact.source_path)
    if actual_sha256 != artifact.expected_sha256:
        raise ValueError(
            f"SHA-256 mismatch for {artifact.source_path}: expected "
            f"{artifact.expected_sha256}, found {actual_sha256}"
        )
    return {
        "name": artifact.name,
        "source_path": str(artifact.source_path),
        "target_path": artifact.target_path.relative_to(repo_root).as_posix(),
        "size_bytes": actual_size,
        "sha256": actual_sha256,
        "source_verified": True,
        "target_verified": False,
    }


def _copy_verified(artifact: CorpusArtifact) -> None:
    target = artifact.target_path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            os.fdopen(fd, "wb") as destination,
            artifact.source_path.open("rb") as source,
        ):
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                destination.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            destination.flush()
            os.fsync(destination.fileno())
            os.fchmod(destination.fileno(), 0o600)
        if digest.hexdigest() != artifact.expected_sha256:
            raise ValueError(f"staged SHA-256 mismatch for {artifact.name}")
        if (
            artifact.expected_size_bytes is not None
            and size != artifact.expected_size_bytes
        ):
            raise ValueError(f"staged size mismatch for {artifact.name}")
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"corpus metadata is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"corpus metadata is invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"corpus metadata must be an object: {path}")
    return value


def _require_object(
    payload: dict[str, Any], key: str, metadata_path: Path
) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{metadata_path} requires object field {key!r}")
    return value


def _require_sha256(payload: dict[str, Any], metadata_path: Path) -> str:
    value = payload.get("sha256")
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{metadata_path} contains an invalid SHA-256")
    return value


def _require_size(payload: dict[str, Any], metadata_path: Path) -> int:
    value = payload.get("size_bytes")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{metadata_path} contains an invalid size_bytes")
    return value


def _require_exact_path(
    payload: dict[str, Any], key: str, expected: Path, metadata_path: Path
) -> None:
    value = payload.get(key)
    if value != expected.as_posix():
        raise ValueError(
            f"{metadata_path} field {key!r} must be {expected.as_posix()!r}"
        )


def _require_exact_name(
    payload: dict[str, Any], key: str, expected: str, metadata_path: Path
) -> None:
    value = payload.get(key)
    if value != expected:
        raise ValueError(f"{metadata_path} field {key!r} must be {expected!r}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--downloads-root",
        type=Path,
        default=Path.home() / "Downloads",
        help="Directory containing source/ and corpus/ (default: ~/Downloads)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy all verified files into their canonical repository paths",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = stage_internal_corpus(
            downloads_root=args.downloads_root,
            apply=args.apply,
        )
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "soc.pingan_internal_corpus_stage.v1",
                    "ready": False,
                    "applied": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
