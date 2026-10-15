"""Continuous accumulation bounds use instants, including preserved offset timestamps."""

from datetime import UTC, datetime

import pytest
from test_soc_corpus_experiment_repository import experiment, members, repository

from soc_agent.db.models import SocCorpusExperimentMemberRow


@pytest.mark.parametrize(
    ("timestamps", "expected"),
    [
        (
            ["2026-08-01T01:00:00+08:00", "2026-07-31T20:00:00Z", None],
            (datetime(2026, 7, 31, 17, tzinfo=UTC), datetime(2026, 7, 31, 20, tzinfo=UTC)),
        ),
        (
            ["2026-08-01T00:00:00Z", "2026-08-01T00:00:00.000001+00:00", None],
            (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 1, microsecond=1, tzinfo=UTC)),
        ),
    ],
)
def test_continuous_range_compares_saved_instants_across_both_batches(tmp_path, timestamps, expected):
    repo = repository(tmp_path)
    rows = members(3)
    rows[1] = rows[1].model_copy(update={"batch": "validation", "validation_tier": "main"})
    store = repo.corpus_experiments()
    store.prepare(experiment(), rows)
    # Preserve historical JSON formatting instead of normalizing the fixture
    # through today's CorpusExperimentMember validator.
    with repo._session_factory() as session:
        for member, timestamp in zip(rows, timestamps, strict=True):
            row = session.get(SocCorpusExperimentMemberRow, ("EXP-test", member.alert_id))
            row.record_payload = {**row.record_payload, "event_time": timestamp}
        session.commit()

    assert store.experiment_event_range("EXP-test") == expected


def test_continuous_range_rejects_missing_times_and_naive_saved_times(tmp_path):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    store.prepare(experiment(), [members(1)[0].model_copy(update={"event_time": None})])
    with pytest.raises(ValueError, match="no event times"):
        store.experiment_event_range("EXP-test")
    with repo._session_factory() as session:
        row = session.get(SocCorpusExperimentMemberRow, ("EXP-test", "0"))
        row.record_payload = {**row.record_payload, "event_time": "2026-08-01T00:00:00"}
        session.commit()
    with pytest.raises(ValueError, match="timezone"):
        store.experiment_event_range("EXP-test")
