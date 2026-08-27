from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.demo.leadership_guide import build_soc_leadership_demo_guide

_INDEX = Path(__file__).resolve().parents[2] / "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json"


@pytest.mark.skipif(not _INDEX.is_file(), reason="local PingAn corpus index unavailable")
def test_leadership_demo_targets_match_the_frozen_corpus() -> None:
    payload = json.loads(_INDEX.read_text(encoding="utf-8"))
    guide = build_soc_leadership_demo_guide(
        alert_groups={item["alert_id"]: item["group_id"] for item in payload["cases"]},
    )

    assert guide.ready is True
    assert guide.primary_chapter_count == 2
    assert guide.backup_chapter_count == 0
    assert [item.sequence for item in guide.chapters] == [1, 2]
    assert all(target.availability == "ready" for chapter in guide.chapters for target in chapter.targets)
    assert [item.expected_memory_use for item in guide.chapters] == ["context_only", "exact_match"]
    assert {item.targets[0].expected_group_id for item in guide.chapters} == {
        "CG-FF9B8E58B0DE",
        "CG-3E54866F029C",
    }


def test_leadership_demo_marks_missing_or_regrouped_targets() -> None:
    guide = build_soc_leadership_demo_guide(
        alert_groups={
            "2480991": "CG-CHANGED",
        },
    )

    first_target = guide.chapters[0].targets[0]
    assert guide.ready is False
    assert first_target.availability == "drifted"
    assert first_target.actual_group_id == "CG-CHANGED"
    assert first_target.drifted_alert_ids == ["2480991"]
    assert guide.chapters[1].targets[0].availability == "unavailable"
