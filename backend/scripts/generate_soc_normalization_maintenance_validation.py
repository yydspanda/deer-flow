"""Regenerate local Runtime Steps 2-5 artifacts from alert samples."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    ActorContext,
    EntrySurface,
    NormalizationBaselineAcceptCommand,
    ServiceRequestContext,
)
from soc_agent.core import (
    SocAnalysisService,
    SocNormalizationMaintenanceService,
    SocNormalizationService,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=Path("../datas"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".deer-flow/soc-runtime-validation/step-05-normalization-maintenance"),
    )
    args = parser.parse_args()
    generate(args.source_dir, args.output_dir)
    return 0


def generate(source_dir: Path, output_dir: Path) -> None:
    samples = [(path, _load_object(path)) for path in sorted(source_dir.glob("*.json"))]
    if not samples:
        raise ValueError(f"no JSON samples found in {source_dir}")

    _write_runtime_steps(samples, validation_root=output_dir.parent)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    maintenance = SocNormalizationMaintenanceService(
        baseline_repository=repository,
        issue_repository=repository,
    )
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-runtime-validation",
            surface=EntrySurface.TEST,
            roles=["soc_engineer"],
        )
    )
    baselines = _accept_observed_baselines(samples, maintenance=maintenance, context=context)
    analysis = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        normalization_maintenance_monitor=maintenance,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[dict[str, Any]] = []
    for source_path, payload in samples:
        run = analysis.analyze(payload, context=context)
        artifact = {
            "schema_version": "soc.runtime_validation_step.v1",
            "step": "normalization_maintenance",
            "source_file": str(source_path),
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "run_id": run.run_id,
            "alert_id": run.alert_id,
            "normalization_monitoring_result": (run.normalization_monitoring_result.model_dump(mode="json", exclude_none=True) if run.normalization_monitoring_result is not None else None),
        }
        artifact_path = output_dir / f"{source_path.stem}.step-05.json"
        artifact_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        generated.append(
            {
                "source": f"datas/{source_path.name}",
                "artifact": artifact_path.name,
                "output_contract": "soc.normalization_monitoring_result.v1",
                "status": "generated",
                "issue_count": len(run.normalization_monitoring_result.issues) if run.normalization_monitoring_result is not None else 0,
            }
        )

    manifest = {
        "schema_version": "soc.runtime_validation.manifest.v1",
        "step": {
            "number": 5,
            "name": "normalization_maintenance",
            "output_contract": "soc.normalization_monitoring_result.v1",
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_count": len(generated),
        "git_ignored": True,
        "baseline_ids": [item.baseline_id for item in baselines],
        "entries": generated,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _update_root_index(output_dir.parent)


def _accept_observed_baselines(
    samples: list[tuple[Path, dict[str, Any]]],
    *,
    maintenance: SocNormalizationMaintenanceService,
    context: ServiceRequestContext,
):
    grouped: dict[tuple[str | None, str, str, str], set[str]] = defaultdict(set)
    for _, payload in samples:
        run = SocAnalysisService().analyze(payload)
        report = run.normalization_report
        if report is None:
            continue
        for observation in report.message_schemas:
            if not observation.parser_name or not observation.parser_version or not observation.schema_fingerprint:
                continue
            key = (
                report.source_system,
                report.adapter,
                observation.parser_name,
                observation.parser_version,
            )
            grouped[key].add(observation.schema_fingerprint)

    baselines = []
    for (source_system, adapter, parser_name, parser_version), fingerprints in grouped.items():
        baselines.append(
            maintenance.accept_baseline(
                NormalizationBaselineAcceptCommand(
                    source_system=source_system,
                    adapter=adapter,
                    parser_name=parser_name,
                    parser_version=parser_version,
                    accepted_fingerprints=sorted(fingerprints),
                    reason="Local reviewed runtime-validation sample set",
                ),
                context=context,
            )
        )
    return baselines


def _write_runtime_steps(
    samples: list[tuple[Path, dict[str, Any]]],
    *,
    validation_root: Path,
) -> None:
    generated_at = datetime.now(UTC).isoformat()
    step_specs = {
        2: (
            "normalization_and_message_parsing",
            "soc.normalization_inspection.v1",
            "step-02-message-parsing",
            "normalization_inspection",
        ),
        3: (
            "fact_reconstruction",
            "soc.fact_reconstruction.v2",
            "step-03-fact-reconstruction",
            "fact_reconstruction",
        ),
        4: (
            "build_analysis_input",
            "soc.llm_analysis_request.v1",
            "step-04-build-analysis-input",
            "analysis_request",
        ),
    }
    entries: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for source_path, payload in samples:
        inspection = SocNormalizationService().inspect(payload)
        run = SocAnalysisService().analyze(payload)
        values = {
            2: inspection.model_dump(mode="json", exclude_none=False),
            3: run.fact_reconstruction.model_dump(mode="json", exclude_none=False),
            4: run.llm_analysis_request.model_dump(mode="json", exclude_none=False),
        }
        source = {
            "file": f"datas/{source_path.name}",
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "size_bytes": source_path.stat().st_size,
        }
        for number, (name, output_contract, directory, output_key) in step_specs.items():
            target_dir = validation_root / directory
            target_dir.mkdir(parents=True, exist_ok=True)
            artifact_name = f"{source_path.stem}.step-{number:02d}.json"
            artifact = {
                "schema_version": f"soc.runtime_validation.step{number:02d}.v2" if number in {2, 3} else "soc.runtime_validation.step04.v1",
                "step": {
                    "number": number,
                    "name": name,
                    "output_contract": output_contract,
                },
                "generated_at": generated_at,
                "source": source,
                "input_reference": {
                    "previous_step": number - 1,
                    "artifact": (f"../step-{number - 1:02d}-input-adapter/{source_path.stem}.step-{number - 1:02d}.json" if number == 2 else f"../{step_specs[number - 1][2]}/{source_path.stem}.step-{number - 1:02d}.json"),
                },
                output_key: values[number],
            }
            (target_dir / artifact_name).write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            entries[number].append(
                {
                    "source": source["file"],
                    "artifact": artifact_name,
                    "output_contract": output_contract,
                    "status": "generated",
                }
            )

    for number, (name, output_contract, directory, _) in step_specs.items():
        manifest = {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "step": {
                "number": number,
                "name": name,
                "output_contract": output_contract,
            },
            "generated_at": generated_at,
            "artifact_count": len(entries[number]),
            "git_ignored": True,
            "entries": entries[number],
        }
        (validation_root / directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"sample {path} must contain a JSON object")
    return value


def _update_root_index(root: Path) -> None:
    index_path = root / "manifest.json"
    index = (
        _load_object(index_path)
        if index_path.exists()
        else {
            "schema_version": "soc.runtime_validation.index.v1",
            "storage_policy": {
                "git_ignored": True,
                "contains_real_alert_derived_data": True,
                "commit_allowed": False,
            },
            "steps": [],
        }
    )
    step = {
        "number": 5,
        "name": "normalization_maintenance",
        "directory": "step-05-normalization-maintenance",
        "manifest": "step-05-normalization-maintenance/manifest.json",
        "status": "generated",
    }
    steps = [item for item in index.get("steps", []) if item.get("number") != 5]
    index["steps"] = sorted([*steps, step], key=lambda item: item["number"])
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
