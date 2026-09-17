#!/usr/bin/env python3
"""Package the four verified corpus files separately from code and private config."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.soc_pingan_stage_internal_corpus import (  # noqa: E402
    CANONICAL_MANIFEST,
    CANONICAL_TARGET,
    MERGED_TARGET,
    PAYLOAD_STORE_TARGET,
    RAW_TARGET,
    WORKBENCH_INDEX,
    _load_artifacts,
    _verify_source,
)

MANIFEST = "CORPUS-TRANSFER.json"
SCHEMA = "soc.pingan_corpus_transfer.v1"
DOWNLOAD_PATHS = {
    f"source/{RAW_TARGET.name}",
    *(
        f"corpus/{path.name}"
        for path in (CANONICAL_TARGET, MERGED_TARGET, PAYLOAD_STORE_TARGET)
    ),
}


def _digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.hexdigest()


def inspect_corpus_transfer(path: Path) -> dict:
    """Stream-verify content and an exact allowlist; never extract or unpickle."""
    path = path.expanduser().resolve()
    seen = set()
    with tarfile.open(path, "r|gz") as archive:
        first = archive.next()
        if (
            first is None
            or first.name != MANIFEST
            or not first.isfile()
            or first.size > 65536
        ):
            raise ValueError("corpus manifest must be the first bounded regular file")
        manifest = json.load(archive.extractfile(first))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
            raise ValueError("unsupported corpus transfer schema")
        entries = manifest.get("files", [])
        if (
            not isinstance(entries, list)
            or len(entries) != 4
            or any(
                not isinstance(item, dict) or not isinstance(item.get("path"), str)
                for item in entries
            )
        ):
            raise ValueError("invalid corpus inventory")
        expected = {item.get("path"): item for item in entries}
        if set(expected) != DOWNLOAD_PATHS:
            raise ValueError("unexpected corpus inventory")
        # Stream mode includes the already-read first member in iteration.
        for member in archive:
            if member is first:
                continue
            if (
                member.name not in expected
                or member.name in seen
                or not member.isfile()
            ):
                raise ValueError("unexpected or duplicate corpus member")
            item = expected[member.name]
            if member.size != item.get("size_bytes") or member.mode != 0o600:
                raise ValueError("corpus size or permissions mismatch")
            if _digest(archive.extractfile(member)) != item.get("sha256"):
                raise ValueError(f"SHA-256 mismatch: {member.name}")
            seen.add(member.name)
        if seen != DOWNLOAD_PATHS:
            raise ValueError("incomplete corpus inventory")
    with path.open("rb") as stream:
        archive_hash = _digest(stream)
    return {
        "schema_version": SCHEMA,
        "path": str(path),
        "sha256": archive_hash,
        "size_bytes": path.stat().st_size,
        "verified_files": len(seen),
        "metadata_hashes": manifest.get("metadata_hashes", {}),
        "contains_code": False,
        "contains_business_database": False,
        "contains_credentials": False,
        "destination_layout": "$HOME/Downloads/{source,corpus}",
    }


def build_corpus_transfer(
    *, root: Path = ROOT, output: Path, compression_level: int = 1
) -> dict:
    root, output = root.expanduser().resolve(), output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"corpus package already exists: {output}")
    if compression_level not in range(1, 10):
        raise ValueError("compression level must be 1..9")
    # Reuse the same metadata/path contract as Mac staging, not another data inventory.
    artifacts = _load_artifacts(repo_root=root, downloads_root=root / "Downloads")
    entries = []
    for artifact in artifacts:
        path = artifact.target_path
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"corpus must be a regular file: {path}")
        if path.suffix == ".sqlite" and any(
            Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
        ):
            raise ValueError("corpus payload store is not quiescent")
        verified = _verify_source(replace(artifact, source_path=path), repo_root=root)
        entries.append(
            {
                "path": artifact.source_path.relative_to(root / "Downloads").as_posix(),
                "size_bytes": verified["size_bytes"],
                "sha256": verified["sha256"],
            }
        )
    metadata = {}
    for path in (CANONICAL_MANIFEST, WORKBENCH_INDEX):
        with (root / path).open("rb") as stream:
            metadata[path.as_posix()] = _digest(stream)
    manifest = {
        "schema_version": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "metadata_hashes": metadata,
        "files": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".corpus-transfer-", dir=output.parent
    ) as directory:
        temporary = Path(directory) / output.name
        with tarfile.open(
            temporary, "w:gz", compresslevel=compression_level
        ) as archive:
            data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mode = len(data), 0o600
            archive.addfile(info, io.BytesIO(data))
            for artifact, entry in zip(artifacts, entries, strict=True):
                info = tarfile.TarInfo(entry["path"])
                info.size, info.mode = entry["size_bytes"], 0o600
                with artifact.target_path.open("rb") as stream:
                    archive.addfile(info, stream)
        temporary.chmod(0o600)
        # Verification before publication also detects changes between hashing and packing.
        report = inspect_corpus_transfer(temporary)
        os.link(temporary, output)
    return {**report, "path": str(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--output", type=Path)
    commands.add_argument("--inspect", type=Path)
    parser.add_argument(
        "--compression-level", type=int, choices=range(1, 10), default=1
    )
    args = parser.parse_args(argv)
    try:
        result = (
            inspect_corpus_transfer(args.inspect)
            if args.inspect
            else build_corpus_transfer(
                output=args.output, compression_level=args.compression_level
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
