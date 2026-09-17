from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest

from soc_agent.llm.admission import SocLLMAdmissionController, SocLLMAdmissionError, soc_model_workload


def wait_for(predicate):
    deadline = monotonic() + 3
    while not predicate():
        assert monotonic() < deadline
        sleep(0.005)


def test_interactive_waiter_precedes_older_background_without_preemption():
    controller = SocLLMAdmissionController(max_concurrency=1, acquire_timeout_seconds=3)
    order = []
    entered, release = Event(), Event()

    def call(name, priority):
        with soc_model_workload(priority), controller.admit():
            order.append(name)
            if name == "running":
                entered.set()
                assert release.wait(3)

    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(call, "running", "background")
        assert entered.wait(3)
        second = pool.submit(call, "queued-batch", "background")
        wait_for(lambda: len(controller._waiting) == 1)
        third = pool.submit(call, "manual-1", "interactive")
        wait_for(lambda: len(controller._waiting) == 2)
        fourth = pool.submit(call, "manual-2", "interactive")
        wait_for(lambda: len(controller._waiting) == 3)
        assert order == ["running"]
        release.set()
        for future in (first, second, third, fourth):
            future.result(3)
    assert order == ["running", "manual-1", "manual-2", "queued-batch"]
    assert not controller._waiting


def test_timeout_and_exceptions_release_tickets_and_capacity():
    controller = SocLLMAdmissionController(max_concurrency=1, acquire_timeout_seconds=0)
    with controller.admit():
        with pytest.raises(SocLLMAdmissionError):
            with soc_model_workload("interactive"), controller.admit():
                pytest.fail("capacity exceeded")
    assert not controller._waiting
    with pytest.raises(ValueError, match="model failed"):
        with controller.admit():
            raise ValueError("model failed")
    with controller.admit():
        pass
    with pytest.raises(ValueError, match="workload"):
        with soc_model_workload("arbitrary-high-priority"):
            pass


def test_nested_workload_scope_restores_and_threads_do_not_inherit_background():
    from soc_agent.llm.admission import _model_workload

    assert _model_workload.get() == "interactive"
    with soc_model_workload("background"):
        assert _model_workload.get() == "background"
        with soc_model_workload("interactive"):
            assert _model_workload.get() == "interactive"
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_model_workload.get).result() == "interactive"
        assert _model_workload.get() == "background"
    assert _model_workload.get() == "interactive"


@pytest.mark.parametrize("completion", ["success", "error", "cancel"])
def test_provider_completion_does_not_release_before_context_exits(completion):
    controller = SocLLMAdmissionController(max_concurrency=1, acquire_timeout_seconds=0)
    future = Future()
    with controller.admit() as slot:
        slot.release_after(future)
        if completion == "cancel":
            future.cancel()
        elif completion == "error":
            future.set_exception(ValueError("provider failed"))
        else:
            future.set_result("finished")
        with pytest.raises(SocLLMAdmissionError):
            with controller.admit():
                pytest.fail("context has not yet released capacity")
    assert controller._active == 0
    with controller.admit():
        pass
