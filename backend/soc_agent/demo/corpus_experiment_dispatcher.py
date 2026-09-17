"""Gateway-owned background dispatcher; CLI and browser only submit control commands."""

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock, Thread

from soc_agent.contracts import ProcessingJobStatus
from soc_agent.core.memory_draft_jobs import DRAFT_WORKLOAD, SocMemoryDraftJobService
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.llm.admission import soc_model_workload

logger = logging.getLogger(__name__)


class CorpusExperimentDispatcher:
    def __init__(self, service: SocCorpusExperimentService, *, draft_jobs: SocMemoryDraftJobService | None = None, max_concurrency: int = 3, interval_seconds: float = 1.0):
        self._service = service
        self._draft_jobs = draft_jobs
        self._draft_turn = True
        self._max_concurrency = max_concurrency
        self._interval = interval_seconds
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._futures: set[Future] = set()
        self._round_cursor = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._pool = ThreadPoolExecutor(max_workers=self._max_concurrency, thread_name_prefix="soc-corpus-batch")
            self._thread = Thread(target=self._loop, name="soc-corpus-dispatch", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join()
        if self._pool is not None:
            # Graceful shutdown preserves in-flight results; abrupt process termination
            # is handled through the durable lease and Runtime request journal instead.
            self._pool.shutdown(wait=True, cancel_futures=True)
        self._thread = None
        self._pool = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Corpus dispatcher tick failed; durable jobs remain recoverable")
            self._stop.wait(self._interval)

    def _tick(self):
        self._service.recover()
        if self._draft_jobs is not None:
            self._draft_jobs.recover()
        for future in list(self._futures):
            if future.done():
                self._futures.remove(future)
                try:
                    future.result()
                except Exception:
                    logger.exception("Corpus dispatcher task failed")
        slots = self._max_concurrency - len(self._futures)
        if slots <= 0 or self._pool is None:
            return
        rounds = self._service.store.list_rounds(state="running", limit=100, offset=self._round_cursor)
        if not rounds:
            self._round_cursor = 0
        has_drafts = self._draft_jobs is not None and bool(self._draft_jobs.jobs.list_workload_jobs(DRAFT_WORKLOAD, statuses=[ProcessingJobStatus.QUEUED], limit=1))
        round_index = 0
        for index in range(slots):
            if self._stop.is_set():
                break
            if has_drafts and (self._draft_turn or not rounds):
                self._futures.add(self._pool.submit(self._dispatch_one, None))
                self._draft_turn = False
            elif rounds:
                round_ = rounds[round_index % len(rounds)]
                self._futures.add(self._pool.submit(self._dispatch_one, round_.round_id))
                round_index += 1
                self._draft_turn = True
            else:
                break
        # Keep later rounds reachable without scanning an unbounded inventory per tick.
        self._round_cursor += min(round_index, len(rounds))

    def _dispatch_one(self, fallback_round_id: str | None):
        # Recheck at execution time: a manual request can arrive while a full pool
        # is busy. Claims still use the existing shared capacity/lease transaction.
        for round_id in self._service.store.list_interactive_round_ids():
            with soc_model_workload("interactive"):
                if self._service.execute_one(round_id):
                    return
        with soc_model_workload("background"):
            if fallback_round_id is not None:
                round_ = self._service.store.get_round(fallback_round_id)
                if round_ is not None and len(round_.selection.alert_ids) == 1:
                    with soc_model_workload("interactive"):
                        self._service.execute_one(fallback_round_id)
                else:
                    self._service.execute_one(fallback_round_id)
            elif self._draft_jobs is not None:
                self._draft_jobs.execute_one()
