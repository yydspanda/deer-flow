import io
import json
import tarfile

import pytest

from scripts.build_pingan_corpus_transfer import (
    build_corpus_transfer,
    inspect_corpus_transfer,
)
from scripts.soc_pingan_stage_internal_corpus import stage_internal_corpus
from scripts.test_soc_pingan_stage_internal_corpus import _write_fixture


def test_corpus_package_uses_downloads_layout_without_source_or_runtime_data(tmp_path):
    root, downloads, payloads = _write_fixture(tmp_path)
    stage_internal_corpus(repo_root=root, downloads_root=downloads, apply=True)
    archive = tmp_path / "corpus.tar.gz"
    report = build_corpus_transfer(root=root, output=archive)
    assert report["verified_files"] == 4
    assert report["contains_code"] is False
    assert report["contains_business_database"] is False
    assert archive.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive) as handle:
        assert set(handle.getnames()) == {
            "CORPUS-TRANSFER.json",
            *[
                f"{'source' if name.endswith('200.pkl') else 'corpus'}/{name}"
                for name in payloads
            ],
        }
    assert inspect_corpus_transfer(archive)["sha256"] == report["sha256"]
    with pytest.raises(FileExistsError):
        build_corpus_transfer(root=root, output=archive)


def test_changed_corpus_is_not_published(tmp_path):
    root, downloads, _ = _write_fixture(tmp_path)
    stage_internal_corpus(repo_root=root, downloads_root=downloads, apply=True)
    (root / "datas/source/full_alert_2026_month_forth_sample_200.pkl").write_bytes(
        b"changed"
    )
    archive = tmp_path / "bad.tar.gz"
    with pytest.raises(ValueError, match="SHA-256"):
        build_corpus_transfer(root=root, output=archive)
    assert not archive.exists()


def test_inspection_rejects_non_corpus_paths_without_extracting(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    manifest = {
        "schema_version": "soc.pingan_corpus_transfer.v1",
        "files": [{"path": "../escape", "size_bytes": 4, "sha256": "a" * 64}],
    }
    data = json.dumps(manifest).encode()
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("CORPUS-TRANSFER.json")
        info.size = len(data)
        handle.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="inventory"):
        inspect_corpus_transfer(archive)
    assert not (tmp_path / "escape").exists()
