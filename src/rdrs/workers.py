from __future__ import annotations

from queue import Full, Queue
from threading import Thread
from typing import Any


class TaskQueue(Queue):
    def __init__(self, maxsize: int = 0):
        super().__init__(maxsize=maxsize)

    @property
    def backpressure(self) -> float:
        if self.maxsize <= 0:
            return 0.0
        return self.qsize() / self.maxsize

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        super().put(item, block=block, timeout=timeout)

    def put_nowait(self, item: Any) -> None:
        super().put_nowait(item)

    def get_nowait(self) -> Any:
        return super().get_nowait()


class WorkerPool:
    def __init__(self, queue: TaskQueue, workers: int = 1):
        self.queue = queue
        self.workers = max(1, workers)
        self._threads: list[Thread] = []

    def run_once(self) -> list[Any]:
        results: list[Any] = []
        for _ in range(self.workers):
            worker = Thread(target=self._worker_loop, args=(results,))
            self._threads.append(worker)
            worker.start()
        for worker in self._threads:
            worker.join()
        self._threads.clear()
        return results

    def _worker_loop(self, results: list[Any]) -> None:
        while not self.queue.empty():
            task = self.queue.get_nowait()
            try:
                if callable(task):
                    results.append(task())
                else:
                    results.append(task)
            finally:
                self.queue.task_done()
