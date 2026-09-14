"""One real semantic review through Runtime; downstream analysis is explicitly stubbed."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from soc_agent.contracts import SensitiveEvidenceMode  # noqa: E402
from soc_agent.contracts.normalization import NormalizationReviewOutput  # noqa: E402
from soc_agent.core.runtime import analyze_alert  # noqa: E402
from soc_agent.integrations.pingan.memory.profile import (  # noqa: E402
    PingAnSocMemoryProfile,
)
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402
from soc_agent.prompts.normalization import build_normalization_prompt  # noqa: E402
from soc_agent.utils.hashing import stable_hash  # noqa: E402

from validation.compact_zeus.audits.normalization_assistance_trial import (  # noqa: E402
    git_value,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alert-id", default="2448412")
    parser.add_argument("--model", default="globalai-deepseek-v4.1-flash")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument(
        "--capture-output",
        action="store_true",
        help="Save model-visible answer in this private local experiment only, without reasoning or headers",
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live required for one real model review")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    directory = ROOT / "validation/compact_zeus/data/corpus"
    index = json.loads(
        (directory / "full_alert_dams_labeled_merged.workbench-index.json").read_text(
            encoding="utf-8"
        )
    )
    expected = next(
        case["payload_hash"]
        for case in index["cases"]
        if case["alert_id"] == args.alert_id
    )
    store = directory / "full_alert_dams_labeled_merged.workbench-payloads.sqlite"
    with sqlite3.connect(store.as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT payload_zlib, payload_hash FROM payloads WHERE alert_id=?",
            (args.alert_id,),
        ).fetchone()
    if row is None or row[1] != expected:
        raise ValueError("index/payload mismatch")
    payload = json.loads(zlib.decompress(row[0]))
    if stable_hash(payload) != expected:
        raise ValueError("payload hash mismatch")

    from deerflow.config import get_app_config
    from dotenv import load_dotenv
    from soc_agent.llm.deerflow_client import DeerFlowLLMChatClient

    load_dotenv(ROOT / ".env")
    config = get_app_config()
    source_hashes = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            Path(__file__),
            ROOT / "backend/soc_agent/llm/normalization.py",
            ROOT / "backend/soc_agent/prompts/normalization.py",
            ROOT / "backend/soc_agent/core/runtime.py",
            ROOT / "backend/soc_agent/contracts/schemas.py",
            ROOT / "backend/soc_agent/contracts/normalization.py",
            ROOT / "backend/soc_agent/normalizers/semantic_observations.py",
            ROOT / "backend/soc_agent/pipeline/extractor.py",
            ROOT / "backend/soc_agent/integrations/pingan/memory/profile.py",
            ROOT / "backend/soc_agent/integrations/pingan/memory/semantic_features.py",
        ]
    }
    client = DeerFlowLLMChatClient(
        app_config=config,
        thinking_enabled=False,
        json_mode_enabled=False,
        attach_tracing=False,
        max_concurrency=1,
        call_timeout_seconds=float(
            os.environ.get("SOC_LLM_CALL_TIMEOUT_SECONDS", "270")
        ),
    )
    if args.capture_output:
        delegate = client

        class CapturingClient:
            def complete(self, messages, *, model_name):
                response = delegate.complete(messages, model_name=model_name)
                write_json(
                    args.output / "raw-answer.local.json",
                    {
                        "content": response.content,
                        "metadata": response.metadata,
                        "usage": response.usage,
                    },
                )
                return response

        client = CapturingClient()
    reviewer = JsonLLMNormalizationReviewer(
        client=client,
        model_name=args.model,
        configuration_hash=stable_hash(
            config.get_model_config(args.model).model_dump(mode="json")
        ),
    )
    before = normalize_alert_payload(payload)
    request = reviewer.prepare(before)
    write_json(args.output / "01-adapter.json", before)
    write_json(args.output / "02-review-request.json", request)
    write_json(args.output / "03-prompt.json", build_normalization_prompt(request))
    write_json(
        args.output / "03-output-schema.json",
        NormalizationReviewOutput.model_json_schema(),
    )
    print(
        f"One real semantic review: {args.alert_id}; main analysis stubbed; no Memory/operational DB writes",
        flush=True,
    )

    def journal(run, request, invocation):
        write_json(
            args.output / f"journal-{invocation.purpose.value}.json",
            {
                "request_hash": stable_hash(request.model_dump(mode="json")),
                "invocation": invocation.model_dump(mode="json"),
                "run_id": run.run_id,
            },
        )

    run = analyze_alert(
        payload,
        normalization_reviewer=reviewer,
        before_provider=journal,
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    write_json(args.output / "04-review-result.json", run.normalization_assistance)
    write_json(args.output / "05-runtime.json", run)
    facets = (
        PingAnSocMemoryProfile(semantic_features=True).project_query_facets(
            run.llm_analysis_request
        )
        if run.llm_analysis_request
        else {}
    )
    write_json(
        args.output / "06-matching-features.json",
        {
            "profile": "pingan.soc/8/v6",
            "facets": facets,
            "quality_verified": False,
            "memory_writes": 0,
        },
    )
    report = run.normalization_assistance
    summary = {
        "schema_version": "soc.normalization_runtime_trial.v1",
        "task_id": "PI-03E",
        "created_at": datetime.now(UTC).isoformat(),
        "alert_id": args.alert_id,
        "code_commit": git_value("rev-parse", "HEAD"),
        "upstream_commit": git_value("rev-parse", "upstream/main"),
        "model": args.model,
        "thinking_requested": False,
        "json_mode_requested": False,
        "max_tokens_requested": config.get_model_config(args.model)
        .model_dump()
        .get("max_tokens"),
        "reference_validation_enabled": request.reference_validation_enabled,
        "config_hash": reviewer.configuration_hash,
        "data_hash": expected,
        "prompt_hash": stable_hash(build_normalization_prompt(request)),
        "source_hashes": source_hashes,
        "hardware": f"{platform.platform()} / Python {platform.python_version()} / CPUs {os.cpu_count()}",
        "command": sys.argv,
        "metrics": {
            "review_status": report.status,
            "changes": len(report.changes),
            "observation_changes": len(report.observation_changes),
            "model_input_present": sum(
                c.model_input_status == "present"
                for c in [*report.changes, *report.observation_changes]
            ),
            "review_usage": report.metadata.get("usage"),
            "runtime_total_ms": run.total_duration_ms,
        },
        "raw_preserved": run.normalized_alert.raw == before.raw,
        "main_analysis_mode": "deterministic_stub_not_quality_evidence",
        "memory_writes": 0,
        "operational_database_writes": 0,
        "fingerprint_quality_verified": False,
    }
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if report.status in {"failed", "skipped"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
