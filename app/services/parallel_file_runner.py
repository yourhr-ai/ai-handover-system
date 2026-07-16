from __future__ import annotations

import os
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Callable, TypeVar


ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


def recommended_file_workers() -> int:
    """Leave one CPU free while keeping small machines usefully parallel."""
    return max(1, min(8, max(2, (os.cpu_count() or 2) - 1)))


def terminate_process_executor(executor: ProcessPoolExecutor) -> None:
    """Stop all workers without waiting for a wedged task to return."""
    processes = list(getattr(executor, "_processes", {}).values())
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=1)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
    executor.shutdown(wait=False, cancel_futures=True)


def run_process_items_with_timeout(
    items: list[ItemT],
    worker: Callable[[ItemT], ResultT],
    *,
    timeout_seconds: float,
    max_workers: int | None = None,
) -> list[tuple[str, ResultT | None]]:
    """Run file-sized jobs in a reusable pool with a hard per-job deadline.

    A timed-out process cannot be killed independently through the public
    ProcessPoolExecutor API. On timeout the pool is recycled, while unfinished
    non-expired jobs are put back on the queue. Pool recreation therefore only
    happens for exceptional/hung work, not once per normal file.
    """
    if not items:
        return []

    worker_count = min(max_workers or recommended_file_workers(), len(items))
    pending = deque(enumerate(items))
    results: list[tuple[str, ResultT | None] | None] = [None] * len(items)
    executor = ProcessPoolExecutor(max_workers=worker_count)
    in_flight: dict = {}

    try:
        while pending or in_flight:
            while pending and len(in_flight) < worker_count:
                index, item = pending.popleft()
                future = executor.submit(worker, item)
                in_flight[future] = (index, item, time.monotonic())

            if not in_flight:
                continue

            next_deadline = min(
                submitted_at + timeout_seconds
                for _index, _item, submitted_at in in_flight.values()
            )
            completed, _ = wait(
                in_flight,
                timeout=max(0.0, next_deadline - time.monotonic()),
                return_when=FIRST_COMPLETED,
            )
            for future in completed:
                index, _item, _submitted_at = in_flight.pop(future)
                try:
                    results[index] = ("ok", future.result())
                except BaseException:
                    results[index] = ("error", None)

            now = time.monotonic()
            expired = [
                future
                for future, (_index, _item, submitted_at) in in_flight.items()
                if now - submitted_at >= timeout_seconds
            ]
            if not expired:
                continue

            expired_set = set(expired)
            retry_items: list[tuple[int, ItemT]] = []
            for future, (index, item, _submitted_at) in list(in_flight.items()):
                if future in expired_set:
                    results[index] = ("timeout", None)
                else:
                    retry_items.append((index, item))
            in_flight.clear()
            terminate_process_executor(executor)
            pending.extendleft(reversed(retry_items))
            if pending:
                executor = ProcessPoolExecutor(max_workers=worker_count)

        executor.shutdown(wait=True)
    except BaseException:
        terminate_process_executor(executor)
        raise

    return [result or ("error", None) for result in results]
