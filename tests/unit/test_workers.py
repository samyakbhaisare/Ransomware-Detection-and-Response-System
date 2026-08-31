from __future__ import annotations

from queue import Full

from rdrs.workers import TaskQueue, WorkerPool


def test_task_queue_tracks_backpressure():
    queue = TaskQueue(maxsize=2)
    queue.put("one")
    queue.put("two")
    assert queue.full() is True
    assert queue.qsize() == 2
    assert 0.0 < queue.backpressure <= 1.0

    try:
        queue.put_nowait("three")
        assert False, "Expected queue.Full when queue is saturated"
    except Full:
        pass


def test_worker_pool_processes_tasks():
    queue = TaskQueue(maxsize=10)
    queue.put(lambda: 2 + 2)
    queue.put(lambda: 5 * 5)

    pool = WorkerPool(queue, workers=2)
    results = pool.run_once()

    assert len(results) == 2
    assert {result for result in results} == {4, 25}
