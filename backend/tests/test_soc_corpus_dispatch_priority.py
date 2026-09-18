from threading import Event
from time import monotonic, sleep

from test_soc_corpus_experiment_repository import repository
from test_soc_corpus_experiments import context, prepare

from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.llm.admission import _model_workload


def wait_for(predicate):
    deadline = monotonic() + 5
    while not predicate():
        assert monotonic() < deadline
        sleep(0.01)


def test_manual_queued_member_survives_restart_without_resuming_batch(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    service = SocCorpusExperimentService(repository=repo, execute=lambda r, m, c: calls.append(m.alert_id) or CorpusExecutionOutcome(run_id="RUN-" + m.alert_id), configuration_provider=lambda _: {})
    batch = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), execution_limit=100), context=context())
    service.pause(batch.round_id, context=context())
    service.request_manual(batch.round_id, "4", context=context())
    service.request_manual(batch.round_id, "4", context=context())
    dispatcher = CorpusExperimentDispatcher(service, interval_seconds=0.02)
    try:
        dispatcher.start()
        wait_for(lambda: service.store.round_progress(batch.round_id).completed_count == 1)
    finally:
        dispatcher.stop()
    assert calls == ["4"]
    assert service.store.get_round(batch.round_id).state == "paused"
    assert service.store.round_progress(batch.round_id).counts["queued"] == 9
    service.start(batch.round_id, context=context())
    while service.execute_one(batch.round_id):
        pass
    assert len(calls) == len(set(calls)) == 10


def test_single_alert_uses_next_slot_before_batch_and_keeps_shared_capacity(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    running, release, manual_running, manual_release = Event(), Event(), Event(), Event()
    order = []

    def execute(round_, member, _):
        order.append((member.alert_id, _model_workload.get()))
        if member.alert_id == "0":
            running.set()
            assert release.wait(5)
        if member.alert_id == "6":
            manual_running.set()
            assert manual_release.wait(5)
        return CorpusExecutionOutcome(run_id="RUN-" + member.alert_id)

    service = SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda _: {}, max_concurrency=1)
    batch = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", group_ids=["group-a"]), concurrency=1, execution_limit=2), context=context())
    service.start(batch.round_id, context=context())
    dispatcher = CorpusExperimentDispatcher(service, max_concurrency=1, interval_seconds=0.02)
    try:
        dispatcher.start()
        assert running.wait(5)
        manual = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", alert_ids=["6"]), concurrency=1), context=context())
        assert service.store.list_interactive_round_ids() == []  # Prepared is not permission to run.
        service.start(manual.round_id, context=context())
        assert service.store.list_interactive_round_ids() == [manual.round_id]
        assert order == [("0", "background")]
        release.set()
        assert manual_running.wait(5)
        assert order == [("0", "background"), ("6", "interactive")]
        assert service.store.active_job_count() == 1
        manual_release.set()
        wait_for(lambda: service.store.round_progress(batch.round_id).completed_count == 2)
    finally:
        release.set()
        manual_release.set()
        dispatcher.stop()
    assert order == [("0", "background"), ("6", "interactive"), ("1", "background")]
    assert service.store.round_progress(manual.round_id).completed_count == 1
    assert service.store.list_interactive_round_ids() == []


def test_paused_single_alert_is_not_prioritized_and_bulk_drafts_stay_background(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    service = SocCorpusExperimentService(repository=repo, execute=lambda *_: calls.append("analysis"), configuration_provider=lambda _: {})
    single = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", alert_ids=["6"])), context=context())
    service.start(single.round_id, context=context())
    service.pause(single.round_id, context=context())
    assert service.store.list_interactive_round_ids() == []

    class Drafts:
        def execute_one(self):
            calls.append(_model_workload.get())

    dispatcher = CorpusExperimentDispatcher(service, draft_jobs=Drafts())
    dispatcher._dispatch_one(None)
    assert calls == ["background"]
