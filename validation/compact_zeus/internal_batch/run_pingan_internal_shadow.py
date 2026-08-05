#!/usr/bin/env python3
"""Run the fixed PI-01E PingAn internal-real paired shadow workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
RUNTIME_BATCH_RUNNER = (
    ROOT / "validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py"
)
PAIRED_EVALUATOR = (
    ROOT / "validation/compact_zeus/internal_batch/evaluate_pingan_shadow.py"
)
PINGAN_DEV_PREFLIGHT = BACKEND_ROOT / "scripts/soc_pingan_dev_preflight.py"
COMPOSITION = BACKEND_ROOT / "samples/enrichment/pingan-internal-shadow.yaml"
ACTION_CONFIGS = (
    BACKEND_ROOT / "samples/mcp/pingan_asset/action_adapters.json",
    BACKEND_ROOT / "samples/mcp/pingan_security_tag/action_adapters.json",
)
EXTENSIONS_CONFIG = BACKEND_ROOT / "samples/mcp/pingan_shadow/extensions.internal.json"

ORCHESTRATION_SCHEMA_VERSION = "soc.pingan_internal_shadow_orchestration.v1"
_RAMP_STAGES = ("5", "50", "all")


@dataclass(frozen=True)
class CommandStep:
    """One existing CLI boundary invoked by the thin orchestrator."""

    step_id: str
    argv: tuple[str, ...]
    cwd: Path


CommandExecutor = Callable[[CommandStep], int]


@dataclass(frozen=True)
class InternalShadowPlan:
    """Serializable plan plus non-serialized executable step objects."""

    payload: dict[str, Any]
    static_steps: tuple[CommandStep, ...]
    live_steps: tuple[CommandStep, ...]


@dataclass(frozen=True)
class InternalShadowPaths:
    """Purpose-specific paths for one paired internal-real evidence set."""

    output_root: Path
    runtime_batch: Path
    investigation_batch: Path
    database_path: Path
    dev_preflight_report: Path
    gate_report: Path
    orchestration_report: Path

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Approved internal PingAn PKL export",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Dedicated Git-ignored directory for this paired evidence set",
    )
    parser.add_argument("--ramp-stage", choices=_RAMP_STAGES, default="5")
    parser.add_argument("--model-name", default="deepseek-v4-flash")
    parser.add_argument("--tenant-id", default="pingan")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute live LLM and read-only Provider calls; omitted prints static plans only",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Independent confirmation for live LLM calls",
    )
    parser.add_argument(
        "--confirm-investigation",
        action="store_true",
        help="Independent confirmation for read-only internal Provider calls",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume both existing paired batch directories without repeating completed work",
    )
    return parser.parse_args(argv)


def build_paths(output_root: Path, *, ramp_stage: str) -> InternalShadowPaths:
    root = output_root.expanduser().resolve()
    return InternalShadowPaths(
        output_root=root,
        runtime_batch=root / "runtime-only",
        investigation_batch=root / "investigation",
        database_path=root / "soc-shadow.db",
        dev_preflight_report=root / "pingan-dev-preflight.json",
        gate_report=root / f"pi-01e-internal-real-{ramp_stage}.json",
        orchestration_report=root / f"orchestration-{ramp_stage}.json",
    )


def build_plan(
    args: argparse.Namespace,
    *,
    python_executable: Path | None = None,
) -> InternalShadowPlan:
    source = args.source.expanduser().resolve()
    paths = build_paths(args.output_root, ramp_stage=args.ramp_stage)
    python = (python_executable or Path(sys.executable)).expanduser().absolute()
    _validate_inputs(args, source=source, python=python)

    common_batch_args = [
        str(python),
        str(RUNTIME_BATCH_RUNNER),
        "--source",
        str(source),
        "--analyzer-mode",
        "llm",
        "--model-name",
        args.model_name,
        "--default-tenant-id",
        args.tenant_id,
        "--workers",
        "1",
        *_limit_args(args.ramp_stage),
    ]
    enrichment_args = [
        "--enrichment-composition",
        str(COMPOSITION),
        *(
            item
            for config in ACTION_CONFIGS
            for item in ("--enrichment-action-config", str(config))
        ),
        "--enrichment-extensions-config",
        str(EXTENSIONS_CONFIG),
    ]

    static_steps = (
        CommandStep(
            step_id="runtime_static_plan",
            argv=tuple(
                [
                    *common_batch_args,
                    "--output-dir",
                    str(paths.runtime_batch),
                    "--plan-only",
                ]
            ),
            cwd=ROOT,
        ),
        CommandStep(
            step_id="investigation_static_plan",
            argv=tuple(
                [
                    *common_batch_args,
                    "--output-dir",
                    str(paths.investigation_batch),
                    *enrichment_args,
                    "--plan-only",
                ]
            ),
            cwd=ROOT,
        ),
    )
    live_steps = (
        CommandStep(
            step_id="pingan_dev_environment_preflight",
            argv=(
                str(python),
                str(PINGAN_DEV_PREFLIGHT),
                "--report-path",
                str(paths.dev_preflight_report),
            ),
            cwd=ROOT,
        ),
        CommandStep(
            step_id="live_mcp_inventory_preflight",
            argv=tuple(
                [
                    *common_batch_args,
                    "--output-dir",
                    str(paths.investigation_batch),
                    *enrichment_args,
                    "--preflight-investigation",
                ]
            ),
            cwd=ROOT,
        ),
        CommandStep(
            step_id="sqlite_migration",
            argv=(
                str(python),
                "-m",
                "soc_agent.cli",
                "db",
                "upgrade",
                "--database-url",
                paths.database_url,
            ),
            cwd=BACKEND_ROOT,
        ),
        CommandStep(
            step_id="runtime_compatibility_batch",
            argv=tuple(
                [
                    *common_batch_args,
                    "--output-dir",
                    str(paths.runtime_batch),
                    "--confirm-live",
                    *(["--resume"] if args.resume else []),
                ]
            ),
            cwd=ROOT,
        ),
        CommandStep(
            step_id="persisted_investigation_batch",
            argv=tuple(
                [
                    *common_batch_args,
                    "--output-dir",
                    str(paths.investigation_batch),
                    "--persist",
                    "--database-url",
                    paths.database_url,
                    *enrichment_args,
                    "--confirm-live",
                    "--confirm-investigation",
                    *(["--resume"] if args.resume else []),
                ]
            ),
            cwd=ROOT,
        ),
        CommandStep(
            step_id="paired_internal_real_gate",
            argv=tuple(
                [
                    str(python),
                    str(PAIRED_EVALUATOR),
                    "--runtime-batch-dir",
                    str(paths.runtime_batch),
                    "--investigation-batch-dir",
                    str(paths.investigation_batch),
                    *enrichment_args,
                    "--acceptance-mode",
                    "internal_real",
                    "--ramp-stage",
                    args.ramp_stage,
                    "--report-path",
                    str(paths.gate_report),
                ]
            ),
            cwd=ROOT,
        ),
    )
    source_sha256 = _sha256_file(source)
    plan_identity = _canonical_sha256(
        {
            "source_sha256": source_sha256,
            "ramp_stage": args.ramp_stage,
            "model_name": args.model_name,
            "tenant_id": args.tenant_id,
            "composition_sha256": _sha256_file(COMPOSITION),
            "action_config_sha256s": [_sha256_file(path) for path in ACTION_CONFIGS],
            "extensions_config_sha256": _sha256_file(EXTENSIONS_CONFIG),
        }
    )
    payload = {
        "schema_version": ORCHESTRATION_SCHEMA_VERSION,
        "orchestration_id": f"PI01E-INT-{plan_identity[:16].upper()}",
        "generated_at": datetime.now(UTC).isoformat(),
        "acceptance_mode": "internal_real",
        "ramp_stage": args.ramp_stage,
        "execute_requested": bool(args.execute),
        "resume": bool(args.resume),
        "source": {
            "path": str(source),
            "sha256": source_sha256,
        },
        "runtime_profile": {
            "analyzer_mode": "llm",
            "model_name": args.model_name,
            "tenant_id": args.tenant_id,
            "workers": 1,
        },
        "configuration": {
            "composition_path": str(COMPOSITION),
            "composition_sha256": _sha256_file(COMPOSITION),
            "action_config_paths": [str(path) for path in ACTION_CONFIGS],
            "action_config_sha256s": [_sha256_file(path) for path in ACTION_CONFIGS],
            "extensions_config_path": str(EXTENSIONS_CONFIG),
            "extensions_config_sha256": _sha256_file(EXTENSIONS_CONFIG),
            "required_result_mode": "real",
        },
        "paths": {
            "output_root": str(paths.output_root),
            "runtime_batch": str(paths.runtime_batch),
            "investigation_batch": str(paths.investigation_batch),
            "database": str(paths.database_path),
            "dev_preflight_report": str(paths.dev_preflight_report),
            "gate_report": str(paths.gate_report),
            "orchestration_report": str(paths.orchestration_report),
        },
        "safety": {
            "live_execution_default": False,
            "live_llm_confirmation_required": True,
            "read_only_provider_confirmation_required": True,
            "high_risk_actions_allowed": False,
            "auto_close_allowed": False,
            "confirmed_memory_write_allowed": False,
            "model_accuracy_evaluated": False,
            "secrets_captured": False,
        },
        "static_steps": [_step_payload(step) for step in static_steps],
        "live_steps": [_step_payload(step) for step in live_steps],
    }
    return InternalShadowPlan(
        payload=payload,
        static_steps=static_steps,
        live_steps=live_steps,
    )


def run_orchestration(
    args: argparse.Namespace,
    *,
    executor: CommandExecutor | None = None,
) -> int:
    try:
        orchestration_plan = build_plan(args)
        plan = orchestration_plan.payload
        if not args.execute:
            command_executor = executor or _execute_plan_step
            for step in orchestration_plan.static_steps:
                return_code = command_executor(step)
                if return_code != 0:
                    return return_code
            plan["status"] = "planned"
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0

        if not args.confirm_live or not args.confirm_investigation:
            raise ValueError(
                "--execute requires both --confirm-live and --confirm-investigation"
            )
        command_executor = executor or _execute_step
        paths = build_paths(args.output_root, ramp_stage=args.ramp_stage)
        _validate_live_output_root(paths, resume=args.resume)
        _prepare_private_directory(paths.output_root)
        report = {
            **plan,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": None,
            "failed_step": None,
            "steps": [],
        }
        _write_private_json(paths.orchestration_report, report)
        for step in orchestration_plan.live_steps:
            started_at = datetime.now(UTC).isoformat()
            try:
                return_code = command_executor(step)
            except KeyboardInterrupt:
                _record_failed_step(
                    report,
                    step_id=step.step_id,
                    started_at=started_at,
                    return_code=130,
                    status="interrupted",
                )
                _write_private_json(paths.orchestration_report, report)
                return 130
            except OSError as exc:
                _record_failed_step(
                    report,
                    step_id=step.step_id,
                    started_at=started_at,
                    return_code=2,
                    status="failed",
                    error_type=type(exc).__name__,
                )
                _write_private_json(paths.orchestration_report, report)
                print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 2
            step_result = {
                "step_id": step.step_id,
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "return_code": return_code,
                "status": "completed" if return_code == 0 else "failed",
            }
            report["steps"].append(step_result)
            if return_code != 0:
                report["status"] = "failed"
                report["failed_step"] = step.step_id
                report["completed_at"] = datetime.now(UTC).isoformat()
                if (
                    step.step_id == "paired_internal_real_gate"
                    and paths.gate_report.is_file()
                ):
                    try:
                        report["gate"] = _gate_summary(
                            json.loads(paths.gate_report.read_text(encoding="utf-8"))
                        )
                    except (OSError, json.JSONDecodeError):
                        pass
                _write_private_json(paths.orchestration_report, report)
                return return_code
            _write_private_json(paths.orchestration_report, report)

        try:
            gate = json.loads(paths.gate_report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _record_failed_step(
                report,
                step_id="paired_gate_report_readback",
                started_at=datetime.now(UTC).isoformat(),
                return_code=2,
                status="failed",
                error_type=type(exc).__name__,
            )
            _write_private_json(paths.orchestration_report, report)
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        gate_status = gate.get("gate_status")
        report["status"] = "completed"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["gate"] = _gate_summary(gate)
        if gate_status != "passed":
            report["status"] = "failed"
            report["failed_step"] = "paired_internal_real_gate"
        _write_private_json(paths.orchestration_report, report)
        return 0 if gate_status == "passed" else 1
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _validate_inputs(
    args: argparse.Namespace,
    *,
    source: Path,
    python: Path,
) -> None:
    if not source.is_file():
        raise ValueError(f"source pickle does not exist: {source}")
    if not python.is_file():
        raise ValueError(f"Python executable does not exist: {python}")
    if not args.model_name.strip():
        raise ValueError("--model-name must not be blank")
    if not args.tenant_id.strip():
        raise ValueError("--tenant-id must not be blank")
    if args.resume and not args.execute:
        raise ValueError("--resume requires --execute")
    for path in (
        RUNTIME_BATCH_RUNNER,
        PAIRED_EVALUATOR,
        PINGAN_DEV_PREFLIGHT,
        COMPOSITION,
        *ACTION_CONFIGS,
        EXTENSIONS_CONFIG,
    ):
        if not path.is_file():
            raise ValueError(f"required PI-01E input does not exist: {path}")


def _limit_args(ramp_stage: str) -> list[str]:
    return [] if ramp_stage == "all" else ["--limit", ramp_stage]


def _step_payload(step: CommandStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "cwd": str(step.cwd),
        "argv": list(step.argv),
        "environment_values_included": False,
    }


def _execute_step(step: CommandStep) -> int:
    print(f"\n==> {step.step_id}", flush=True)
    completed = subprocess.run(step.argv, cwd=step.cwd, check=False)
    return completed.returncode


def _execute_plan_step(step: CommandStep) -> int:
    completed = subprocess.run(
        step.argv,
        cwd=step.cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, end="", file=sys.stderr)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


def _record_failed_step(
    report: dict[str, Any],
    *,
    step_id: str,
    started_at: str,
    return_code: int,
    status: str,
    error_type: str | None = None,
) -> None:
    step_result: dict[str, Any] = {
        "step_id": step_id,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "return_code": return_code,
        "status": status,
    }
    if error_type is not None:
        step_result["error_type"] = error_type
    report["steps"].append(step_result)
    report["status"] = status
    report["failed_step"] = step_id
    report["completed_at"] = datetime.now(UTC).isoformat()


def _gate_summary(gate: dict[str, Any]) -> dict[str, Any]:
    claims = gate.get("claims") or {}
    return {
        "report_id": gate.get("report_id"),
        "gate_status": gate.get("gate_status"),
        "blocking_failure_ids": gate.get("blocking_failure_ids"),
        "next_stage": claims.get("next_stage"),
        "next_stage_requires_human_review": claims.get(
            "next_stage_requires_human_review"
        ),
    }


def _prepare_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _validate_live_output_root(
    paths: InternalShadowPaths,
    *,
    resume: bool,
) -> None:
    root = paths.output_root
    if resume:
        if not root.is_dir() or not paths.orchestration_report.is_file():
            raise ValueError(
                f"--resume requires the existing output directory and matching orchestration report: {paths.orchestration_report}"
            )
        return
    if not root.exists():
        return
    if not root.is_dir():
        raise ValueError(f"--output-root must be a directory: {root}")
    if any(root.iterdir()):
        raise ValueError(
            "fresh live execution requires a new or empty --output-root; use --resume only for the same interrupted evidence set"
        )


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    _prepare_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    return run_orchestration(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
