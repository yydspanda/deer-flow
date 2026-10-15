"""Both batches display saved accumulation outcomes, including legacy omissions."""

import pytest
from test_soc_memory_patterns import _observe, _run, _service

from soc_agent.contracts.processing_jobs import ProcessingJobStatus
from soc_agent.db.corpus_experiments import CorpusRunJob
from soc_agent.demo.corpus_workbench import _execution_view
from soc_agent.memory.patterns import InMemoryMemoryPatternRepository


def memory_phase(*, batch, reason=None, active=False, observation=None, replay=None):
    run = _run(5)
    execution = _execution_view(
        alert_id=run.alert_id,
        run=run,
        observation=observation,
        replay=replay,
        candidate=None,
        pattern_job=CorpusRunJob(status=ProcessingJobStatus.ANALYZING if active else ProcessingJobStatus.COMPLETED, batch=batch, pattern_reason=reason),
        pattern_active=active,
    )
    return next(phase for phase in execution.phases if phase.phase == "memory")


def test_new_validation_attempt_displays_actual_active_accumulation():
    phase = memory_phase(batch="validation", active=True)
    assert phase.status == "running"
    assert "正在写入" in phase.summary


def test_historical_validation_omission_keeps_its_saved_explanation():
    phase = memory_phase(batch="validation", reason="validation_learning_disabled")
    assert phase.status == "skipped"
    assert "仅验证经验效果" in phase.summary


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_semantic_failure_explains_deferred_learning_in_both_batches(batch):
    phase = memory_phase(batch=batch, reason="semantic_review_failed_learning_deferred")
    assert phase.status == "skipped"
    assert "语义核对失败" in phase.summary
    assert "暂缓积累" in phase.summary


def test_validation_observation_displays_effective_support():
    repository = InMemoryMemoryPatternRepository()
    patterns = _service(repository, threshold=100)
    for index in range(1, 6):
        result = _observe(patterns, _run(index), transport_ref=f"learning-display:{index}")
    phase = memory_phase(batch="validation", observation=result.observation, replay=result)
    assert phase.status == "success"
    assert phase.metrics["support_count"] == 5
    assert phase.metrics["distinct_sources"] == 5
    assert "累计 5 条" in phase.summary


def test_deferred_attempt_does_not_claim_existing_observation_as_its_success():
    repository = InMemoryMemoryPatternRepository()
    result = _observe(_service(repository), _run(5), transport_ref="previous-success")
    phase = memory_phase(batch="validation", reason="semantic_review_failed_learning_deferred", observation=result.observation, replay=result)
    assert phase.status == "skipped"
    assert "暂缓积累" in phase.summary
    assert "support_count" not in phase.metrics
