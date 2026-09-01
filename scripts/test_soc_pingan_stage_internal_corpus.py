from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest
from scripts.soc_pingan_stage_internal_corpus import stage_internal_corpus


RAW_NAME = "full_alert_2026_month_forth_sample_200.pkl"
CANONICAL_NAME = "full_alert_validation_corpus.pkl"
MERGED_NAME = "full_alert_dams_labeled_merged.pkl"
PAYLOAD_STORE_NAME = "full_alert_dams_labeled_merged.workbench-payloads.sqlite"


def test_dry_run_validates_external_corpus_without_copying(tmp_path: Path) -> None:
    repo_root, downloads_root, expected = _write_fixture(tmp_path)

    report = stage_internal_corpus(
        repo_root=repo_root,
        downloads_root=downloads_root,
        apply=False,
    )

    assert report["ready"] is True
    assert report["applied"] is False
    assert {item["name"] for item in report["files"]} == set(expected)
    assert all(item["source_verified"] is True for item in report["files"])
    assert not (repo_root / "datas/source" / RAW_NAME).exists()
    assert not (
        repo_root / "validation/compact_zeus/data/corpus" / CANONICAL_NAME
    ).exists()


def test_apply_copies_verified_corpus_with_private_permissions(
    tmp_path: Path,
) -> None:
    repo_root, downloads_root, expected = _write_fixture(tmp_path)

    report = stage_internal_corpus(
        repo_root=repo_root,
        downloads_root=downloads_root,
        apply=True,
    )

    assert report["ready"] is True
    assert report["applied"] is True
    for item in report["files"]:
        target = repo_root / item["target_path"]
        assert target.read_bytes() == expected[item["name"]]
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert item["target_verified"] is True


def test_missing_external_corpus_fails_closed(tmp_path: Path) -> None:
    repo_root, downloads_root, _ = _write_fixture(tmp_path)
    (downloads_root / "corpus" / MERGED_NAME).unlink()

    with pytest.raises(ValueError, match="external corpus files are missing"):
        stage_internal_corpus(
            repo_root=repo_root,
            downloads_root=downloads_root,
            apply=True,
        )

    assert not (repo_root / "datas/source" / RAW_NAME).exists()


def test_hash_mismatch_does_not_replace_existing_target(tmp_path: Path) -> None:
    repo_root, downloads_root, _ = _write_fixture(tmp_path)
    target = repo_root / "datas/source" / RAW_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing-corpus")
    (downloads_root / "source" / RAW_NAME).write_bytes(b"wrong-corpus")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        stage_internal_corpus(
            repo_root=repo_root,
            downloads_root=downloads_root,
            apply=True,
        )

    assert target.read_bytes() == b"existing-corpus"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes]]:
    repo_root = tmp_path / "repo"
    downloads_root = tmp_path / "Downloads"
    corpus_dir = repo_root / "validation/compact_zeus/data/corpus"
    corpus_dir.mkdir(parents=True)
    (downloads_root / "source").mkdir(parents=True)
    (downloads_root / "corpus").mkdir(parents=True)

    payloads = {
        RAW_NAME: b"raw-pkl",
        CANONICAL_NAME: b"canonical-pkl",
        MERGED_NAME: b"merged-pkl",
        PAYLOAD_STORE_NAME: b"payload-store",
    }
    (downloads_root / "source" / RAW_NAME).write_bytes(payloads[RAW_NAME])
    for name in (CANONICAL_NAME, MERGED_NAME, PAYLOAD_STORE_NAME):
        (downloads_root / "corpus" / name).write_bytes(payloads[name])

    canonical_manifest = {
        "schema_version": "soc.validation.alert_corpus.v1",
        "source": {
            "path": f"datas/source/{RAW_NAME}",
            "sha256": _sha256(payloads[RAW_NAME]),
        },
        "output": {
            "path": f"validation/compact_zeus/data/corpus/{CANONICAL_NAME}",
            "sha256": _sha256(payloads[CANONICAL_NAME]),
        },
    }
    (corpus_dir / "full_alert_validation_corpus.manifest.json").write_text(
        json.dumps(canonical_manifest),
        encoding="utf-8",
    )
    workbench_index = {
        "schema_version": "soc.corpus_workbench_index.v3",
        "source": {
            "file_name": MERGED_NAME,
            "size_bytes": len(payloads[MERGED_NAME]),
            "sha256": _sha256(payloads[MERGED_NAME]),
        },
        "payload_store": {
            "file_name": PAYLOAD_STORE_NAME,
            "size_bytes": len(payloads[PAYLOAD_STORE_NAME]),
            "sha256": _sha256(payloads[PAYLOAD_STORE_NAME]),
        },
    }
    (corpus_dir / "full_alert_dams_labeled_merged.workbench-index.json").write_text(
        json.dumps(workbench_index),
        encoding="utf-8",
    )
    return repo_root, downloads_root, payloads


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
