#!/usr/bin/env python3
"""Build the no-network Apple Silicon toolchain bundle for PingAn DEV."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DEFAULT_OUTPUT_DIR = BACKEND / ".deer-flow" / "internal-transfer"

UV_VERSION = "0.10.9"
UV_ARCHIVE_NAME = "uv-aarch64-apple-darwin.tar.gz"
UV_URL = (
    f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{UV_ARCHIVE_NAME}"
)
UV_SHA256_URL = f"{UV_URL}.sha256"

PYTHON_VERSION = "3.12.3"
PYTHON_BUILD = "20240415"
PYTHON_ARCHIVE_NAME = (
    f"cpython-{PYTHON_VERSION}+{PYTHON_BUILD}-aarch64-apple-darwin-install_only.tar.gz"
)
PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PYTHON_BUILD}/cpython-{PYTHON_VERSION}%2B{PYTHON_BUILD}-aarch64-apple-darwin-install_only.tar.gz"
)
PYTHON_SHA256_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_BUILD}/SHA256SUMS"

BUNDLE_SCHEMA = "soc.pingan_macos_offline_bundle.v1"
BUNDLE_ROOT = "deer-flow-pingan-macos-arm64-offline"


def build_bundle(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    uv_executable: str = "uv",
    uv_archive: Path | None = None,
    uv_sha256_file: Path | None = None,
    python_archive: Path | None = None,
    python_sha256_file: Path | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    _require_clean_lock(uv_executable)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    with tempfile.TemporaryDirectory(
        prefix="pingan-macos-offline-", dir=output_dir
    ) as raw_temp:
        staging = Path(raw_temp)
        downloads = staging / "downloads"
        downloads.mkdir(mode=0o700)
        selected_uv = _obtain_verified_artifact(
            supplied=uv_archive,
            destination=downloads / UV_ARCHIVE_NAME,
            url=UV_URL,
            expected_sha256=_uv_expected_sha256(staging, supplied=uv_sha256_file),
        )
        selected_python = _obtain_verified_artifact(
            supplied=python_archive,
            destination=downloads / PYTHON_ARCHIVE_NAME,
            url=PYTHON_URL,
            expected_sha256=_python_expected_sha256(
                staging, supplied=python_sha256_file
            ),
        )

        cache_dir = staging / "uv-cache"
        online_env = staging / "online-target-env"
        _run_target_sync(
            uv_executable=uv_executable,
            cache_dir=cache_dir,
            environment_dir=online_env,
            offline=False,
        )
        shutil.rmtree(online_env, ignore_errors=True)
        verify_env = staging / "offline-verification-env"
        _run_target_sync(
            uv_executable=uv_executable,
            cache_dir=cache_dir,
            environment_dir=verify_env,
            offline=True,
        )
        shutil.rmtree(verify_env, ignore_errors=True)

        cache_archive = staging / "uv-cache-macos-arm64-cp312.tar.gz"
        _write_directory_archive(cache_archive, cache_dir, arcname="uv-cache")
        artifacts = [selected_uv, selected_python, cache_archive]
        artifact_records = [_file_record(path) for path in artifacts]
        lock_sha256 = _sha256_file(BACKEND / "uv.lock")
        manifest = {
            "schema_version": BUNDLE_SCHEMA,
            "created_at": datetime.now(UTC).isoformat(),
            "target": {
                "os": "macos",
                "architecture": "arm64",
                "python": PYTHON_VERSION,
                "uv": UV_VERSION,
            },
            "dependency_profile": {
                "project": "backend",
                "lock_sha256": lock_sha256,
                "extra": "pingan-dev",
                "includes_default_dev_group": True,
                "platform": "aarch64-apple-darwin",
            },
            "artifacts": artifact_records,
            "offline_sync_verified": True,
            "network_required_on_target": False,
            "requires_admin": False,
        }
        bundle = output_dir / f"deer-flow-pingan-macos-arm64-offline-{timestamp}.tar.gz"
        _write_bundle_archive(
            bundle,
            artifacts=artifacts,
            manifest=manifest,
            installer=_installer_script(lock_sha256=lock_sha256),
            readme=_bundle_readme(),
        )
        report = {
            **manifest,
            "bundle": _file_record(bundle),
        }
        report_path = output_dir / f"macos-offline-report-{timestamp}.json"
        _write_private_json(report_path, report)
        report["report_path"] = str(report_path)
        return report


def inspect_bundle(path: Path) -> dict[str, Any]:
    bundle = path.expanduser().resolve()
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        _assert_safe_members(members)
        names = {member.name for member in members}
        manifest_name = f"{BUNDLE_ROOT}/MANIFEST.json"
        if manifest_name not in names:
            raise ValueError("offline bundle manifest is missing")
        manifest_stream = archive.extractfile(manifest_name)
        if manifest_stream is None:
            raise ValueError("offline bundle manifest cannot be read")
        manifest = json.loads(manifest_stream.read().decode("utf-8"))
        if manifest.get("schema_version") != BUNDLE_SCHEMA:
            raise ValueError("offline bundle schema is unsupported")
        for item in manifest.get("artifacts", []):
            member_name = f"{BUNDLE_ROOT}/artifacts/{item['name']}"
            stream = archive.extractfile(member_name)
            if stream is None:
                raise ValueError(f"offline artifact is missing: {item['name']}")
            digest = hashlib.sha256(stream.read()).hexdigest()
            if digest != item["sha256"]:
                raise ValueError(f"offline artifact digest mismatch: {item['name']}")
    return {
        "bundle": str(bundle),
        "bundle_sha256": _sha256_file(bundle),
        "bundle_size_bytes": bundle.stat().st_size,
        "artifact_count": len(manifest["artifacts"]),
        "target": manifest["target"],
        "offline_sync_verified": manifest["offline_sync_verified"],
        "safe_member_paths": True,
        "manifest_valid": True,
    }


def _run_target_sync(
    *,
    uv_executable: str,
    cache_dir: Path,
    environment_dir: Path,
    offline: bool,
) -> None:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["UV_CACHE_DIR"] = str(cache_dir)
    env["UV_PROJECT_ENVIRONMENT"] = str(environment_dir)
    command = [
        uv_executable,
        "sync",
        "--locked",
        "--extra",
        "pingan-dev",
        "--python",
        PYTHON_VERSION,
        "--python-platform",
        "aarch64-apple-darwin",
        "--link-mode",
        "copy",
        "--directory",
        str(BACKEND),
    ]
    if offline:
        command.extend(["--offline", "--no-python-downloads"])
    subprocess.run(command, check=True, env=env)


def _uv_expected_sha256(staging: Path, *, supplied: Path | None) -> str:
    checksum = staging / "uv.sha256"
    _obtain_text_sidecar(supplied=supplied, destination=checksum, url=UV_SHA256_URL)
    first = checksum.read_text(encoding="utf-8").strip().split()[0]
    return _validate_digest(first, label="uv")


def _python_expected_sha256(staging: Path, *, supplied: Path | None) -> str:
    checksums = staging / "python-sha256sums.txt"
    _obtain_text_sidecar(
        supplied=supplied, destination=checksums, url=PYTHON_SHA256_URL
    )
    for line in checksums.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.strip().partition("  ")
        if name == PYTHON_ARCHIVE_NAME:
            return _validate_digest(digest, label="Python")
    raise ValueError("official Python checksum manifest omitted the selected archive")


def _obtain_text_sidecar(*, supplied: Path | None, destination: Path, url: str) -> None:
    if supplied is None:
        _download(url, destination)
        return
    source = supplied.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"supplied checksum file does not exist: {source}")
    shutil.copyfile(source, destination)


def _obtain_verified_artifact(
    *,
    supplied: Path | None,
    destination: Path,
    url: str,
    expected_sha256: str,
) -> Path:
    if supplied is None:
        _download(url, destination)
    else:
        source = supplied.expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"supplied artifact does not exist: {source}")
        shutil.copyfile(source, destination)
    actual = _sha256_file(destination)
    if actual != expected_sha256:
        raise ValueError(f"artifact checksum mismatch for {destination.name}")
    destination.chmod(0o600)
    return destination


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "deer-flow-pingan-offline-builder/1"}
    )
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            temporary.open("wb") as output,
        ):  # noqa: S310 - pinned HTTPS sources
            shutil.copyfileobj(response, output)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_directory_archive(target: Path, source: Path, *, arcname: str) -> None:
    with tarfile.open(target, "w:gz", compresslevel=6, dereference=True) as archive:
        archive.add(
            source, arcname=arcname, recursive=True, filter=_normalized_tar_info
        )
    target.chmod(0o600)


def _write_bundle_archive(
    target: Path,
    *,
    artifacts: list[Path],
    manifest: dict[str, Any],
    installer: str,
    readme: str,
) -> None:
    with tarfile.open(target, "w:gz", compresslevel=6) as archive:
        _add_bytes(
            archive, f"{BUNDLE_ROOT}/MANIFEST.json", _json_bytes(manifest), mode=0o600
        )
        _add_bytes(
            archive,
            f"{BUNDLE_ROOT}/SHA256SUMS",
            _checksums(artifacts).encode(),
            mode=0o600,
        )
        _add_bytes(
            archive, f"{BUNDLE_ROOT}/install-offline.sh", installer.encode(), mode=0o700
        )
        _add_bytes(archive, f"{BUNDLE_ROOT}/README.md", readme.encode(), mode=0o600)
        for artifact in artifacts:
            info = archive.gettarinfo(
                str(artifact), arcname=f"{BUNDLE_ROOT}/artifacts/{artifact.name}"
            )
            info = _normalized_tar_info(info)
            info.mode = 0o600
            with artifact.open("rb") as stream:
                archive.addfile(info, stream)
    target.chmod(0o600)


def _installer_script(*, lock_sha256: str | None = None) -> str:
    expected_lock_sha256 = lock_sha256 or _sha256_file(BACKEND / "uv.lock")
    return f"""#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "error: this bundle supports Apple Silicon macOS (Darwin arm64) only" >&2
  exit 2
fi

BUNDLE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=${{1:-}}
if [ -z "$REPO_ROOT" ]; then
  echo "usage: $0 /absolute/path/to/deer-flow" >&2
  exit 2
fi
REPO_ROOT=$(CDPATH= cd -- "$REPO_ROOT" && pwd)
if [ ! -f "$REPO_ROOT/backend/uv.lock" ] || [ ! -f "$REPO_ROOT/AGENTS.md" ]; then
  echo "error: target is not a DeerFlow source checkout" >&2
  exit 2
fi

ACTUAL_LOCK_SHA256=$(shasum -a 256 "$REPO_ROOT/backend/uv.lock" | awk '{{print $1}}')
if [ "$ACTUAL_LOCK_SHA256" != "{expected_lock_sha256}" ]; then
  echo "error: backend/uv.lock does not match this offline dependency bundle" >&2
  exit 2
fi

(cd "$BUNDLE_DIR/artifacts" && shasum -a 256 -c "$BUNDLE_DIR/SHA256SUMS")

TOOLCHAIN="$REPO_ROOT/backend/.deer-flow/toolchain"
PYTHON_HOME="$TOOLCHAIN/cpython-{PYTHON_VERSION}-macos-arm64"
UV_HOME="$TOOLCHAIN/uv-{UV_VERSION}-macos-arm64"
CACHE_ROOT="$REPO_ROOT/backend/.deer-flow/offline"
CACHE_DIR="$CACHE_ROOT/uv-cache"
mkdir -p "$PYTHON_HOME" "$UV_HOME" "$CACHE_ROOT"

rm -rf "$PYTHON_HOME" "$UV_HOME" "$CACHE_DIR"
mkdir -p "$PYTHON_HOME" "$UV_HOME" "$CACHE_ROOT"
tar -xzf "$BUNDLE_DIR/artifacts/{PYTHON_ARCHIVE_NAME}" -C "$PYTHON_HOME" --strip-components=1
tar -xzf "$BUNDLE_DIR/artifacts/{UV_ARCHIVE_NAME}" -C "$UV_HOME" --strip-components=1
tar -xzf "$BUNDLE_DIR/artifacts/uv-cache-macos-arm64-cp312.tar.gz" -C "$CACHE_ROOT"
chmod 700 "$UV_HOME/uv" "$UV_HOME/uvx"

PYTHON_BIN="$PYTHON_HOME/bin/python3"
UV_BIN="$UV_HOME/uv"
"$PYTHON_BIN" -c 'import platform,sys; assert sys.version_info[:3] == (3,12,3); assert platform.machine() == "arm64"'
"$UV_BIN" --version

rm -rf "$REPO_ROOT/backend/.venv"
UV_CACHE_DIR="$CACHE_DIR" UV_PYTHON_DOWNLOADS=never \
  "$UV_BIN" sync --directory "$REPO_ROOT/backend" --locked --extra pingan-dev \
  --python "$PYTHON_BIN" --offline --no-python-downloads --link-mode copy

(cd "$REPO_ROOT/backend" && ./.venv/bin/python -c \
  'import httpx,pandas,pydantic; import soc_agent; print("offline backend imports: OK")')
"$REPO_ROOT/backend/.venv/bin/python" \
  "$REPO_ROOT/backend/scripts/soc_pingan_local_paths.py"

echo "offline PingAn DEV toolchain installed"
echo "python: $PYTHON_BIN"
echo "uv:     $UV_BIN"
echo "venv:   $REPO_ROOT/backend/.venv"
"""


def _bundle_readme() -> str:
    return f"""# PingAn DEV macOS arm64 offline toolchain

This bundle installs CPython {PYTHON_VERSION}, uv {UV_VERSION}, and the exact
`backend/uv.lock` dependency set for `--extra pingan-dev` without public network
access or administrator privileges. It is separate from the DeerFlow source and
private-overlay archives.

```bash
tar -xzf deer-flow-pingan-macos-arm64-offline-<timestamp>.tar.gz
cd {BUNDLE_ROOT}
./install-offline.sh /Users/<your-user>/deer-flow
```

The installer resolves the supplied checkout path at runtime. It never assumes
a fixed developer home directory, does not modify `sec_know_model`, and creates only:

- `backend/.deer-flow/toolchain/`
- `backend/.deer-flow/offline/uv-cache/`
- `backend/.venv/`

`uv sync` is executed with `--offline`, `--no-python-downloads`, and a bundle-
owned cache. A successful build report means a fresh target-platform sync was
rehearsed offline on the source machine; final execution still must be verified
on the internal Apple Silicon Mac.
"""


def _require_clean_lock(uv_executable: str) -> None:
    lock = BACKEND / "uv.lock"
    if not lock.is_file():
        raise ValueError("backend/uv.lock is required")
    with tempfile.TemporaryDirectory(prefix="pingan-lock-check-") as cache:
        env = os.environ.copy()
        env["UV_CACHE_DIR"] = cache
        subprocess.run(
            [
                uv_executable,
                "lock",
                "--check",
                "--offline",
                "--directory",
                str(BACKEND),
            ],
            check=True,
            env=env,
        )


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _checksums(paths: list[Path]) -> str:
    return "".join(f"{_sha256_file(path)}  {path.name}\n" for path in paths)


def _validate_digest(value: str, *, label: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError(f"official {label} checksum is invalid")
    return normalized


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(_json_bytes(value))
    path.chmod(0o600)


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes, *, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    import io

    archive.addfile(info, io.BytesIO(data))


def _normalized_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    if info.isdir():
        info.mode = 0o700
    elif info.isfile():
        info.mode = 0o600
    return info


def _assert_safe_members(members: list[tarfile.TarInfo]) -> None:
    for member in members:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != BUNDLE_ROOT
        ):
            raise ValueError(f"unsafe offline bundle member: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError("offline bundle must not contain links")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--uv-executable", default="uv")
    parser.add_argument("--uv-archive", type=Path)
    parser.add_argument("--uv-sha256-file", type=Path)
    parser.add_argument("--python-archive", type=Path)
    parser.add_argument("--python-sha256-file", type=Path)
    parser.add_argument("--inspect", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.inspect:
        result = inspect_bundle(args.inspect)
    else:
        result = build_bundle(
            output_dir=args.output_dir,
            uv_executable=args.uv_executable,
            uv_archive=args.uv_archive,
            uv_sha256_file=args.uv_sha256_file,
            python_archive=args.python_archive,
            python_sha256_file=args.python_sha256_file,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
