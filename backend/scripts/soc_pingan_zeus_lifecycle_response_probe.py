"""Print and privately save one complete ZEUS lifecycle provider response."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.legacy_compat import (  # noqa: E402
    PingAnLegacyProviderMode,
    PingAnLegacyTaskRequest,
    PingAnLegacyWorkerSettings,
    build_pingan_lifecycle_port,
)
from soc_agent.integrations.pingan.legacy_compat.zeus_lifecycle import (  # noqa: E402
    PingAnAlertLifecyclePort,
)

DEFAULT_OUTPUT_PATH = BACKEND_ROOT / ".deer-flow/soc-internal-validation/legacy-compat/lifecycle-response.local.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help=("Acknowledge one real read-only ZEUS request and local storage of its complete potentially sensitive response"),
    )
    parser.add_argument(
        "--request-file",
        required=True,
        type=Path,
        help="Private 0600 task request prepared for live acceptance",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Private .local.json destination for the complete response",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing private response file",
    )
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required")

    try:
        request = _read_private_request(args.request_file)
        response = query_complete_lifecycle_response(request.alert_id)
        destination = _write_private_response(
            args.output_path,
            response,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        print(
            f"error: ZEUS lifecycle response probe failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    print(
        f"Complete provider response saved with mode 0600: {destination}",
        file=sys.stderr,
    )
    return 0


def query_complete_lifecycle_response(
    alert_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    port: PingAnAlertLifecyclePort | None = None,
) -> dict[str, Any]:
    """Return the provider JSON object without applying lifecycle projection."""

    normalized_alert_id = alert_id.strip()
    if not normalized_alert_id:
        raise ValueError("ZEUS lifecycle response probe requires an alert ID")
    lifecycle_port = port or _build_internal_port(environ)
    if lifecycle_port.mocked:
        raise ValueError("ZEUS lifecycle response probe requires the internal Provider")
    response = lifecycle_port.query(alert_id=normalized_alert_id)
    if not isinstance(response, Mapping):
        raise ValueError("ZEUS lifecycle Provider returned a non-object response")
    return dict(response)


def _build_internal_port(
    environ: Mapping[str, str] | None,
) -> PingAnAlertLifecyclePort:
    values = dict(os.environ if environ is None else environ)
    settings = PingAnLegacyWorkerSettings.from_env(values)
    if settings.lifecycle_mode is not PingAnLegacyProviderMode.INTERNAL:
        raise ValueError("SOC_PINGAN_LEGACY_LIFECYCLE_MODE must equal internal")
    return build_pingan_lifecycle_port(settings, environ=values)


def _read_private_request(path: Path) -> PingAnLegacyTaskRequest:
    if path.is_symlink():
        raise ValueError("lifecycle response probe request must not be a symbolic link")
    if not path.name.lower().endswith(".local.json"):
        raise ValueError("lifecycle response probe request filename must end in .local.json")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("lifecycle response probe request permissions must be 0600 or stricter")
    value = json.loads(path.read_text(encoding="utf-8"))
    return PingAnLegacyTaskRequest.model_validate(value)


def _write_private_response(
    path: Path,
    response: Mapping[str, Any],
    *,
    overwrite: bool,
) -> Path:
    candidate = path.expanduser()
    if not candidate.name.lower().endswith(".local.json"):
        raise ValueError("lifecycle response output filename must end in .local.json")
    if candidate.is_symlink():
        raise ValueError("lifecycle response output must not be a symbolic link")
    destination = candidate.resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"lifecycle response output already exists: {destination}; pass --overwrite to replace it")
    if destination.exists() and not destination.is_file():
        raise ValueError("lifecycle response output must be a regular file")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (
        json.dumps(
            response,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    raise SystemExit(main())
