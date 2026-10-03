"""Process-level parallelism for the per-remote loops of the build (R12, D19).

Every stage of ``rl build`` is a loop over the remotes in sorted order whose
iterations are independent: a remote's verdict, artifact and index summary are
pure functions of its own file. That is the only fact this module relies on.
:func:`ordered_map` runs such a loop in worker processes and hands the results
back **in the order of the input**, never the order the workers finish in, so
every byte a stage writes, every ERROR line it prints and every warning it
records is the same whatever the worker count and however the scheduler
interleaves them (R12). With one worker it is a plain in-process generator --
the code path that existed before the pool did.

Memory stays bounded: results are consumed lazily and only a window of chunks
is ever in flight, so a slow consumer stalls the workers instead of letting
the whole corpus's results pile up in the parent.

A pool lives for one ``ordered_map`` call. Workers are forked when it starts,
so they see the module state, working directory and environment of that moment
-- never a stale copy from an earlier call -- and nothing lingers afterwards.
Starting a pool costs milliseconds against stages that take seconds.

How many workers (first match wins):

* ``--jobs N`` on the command line (``configure``);
* the ``RL_JOBS`` environment variable (``RL_JOBS=1`` forces the serial path,
  which is what to use under a debugger or when reading a traceback);
* otherwise ``min(usable CPUs, 8)``, where "usable" respects ``taskset`` and
  container CPU masks (``os.sched_getaffinity``) rather than the host's total,
  and a corpus too small to repay the pool (``AUTO_MIN_ITEMS``) stays serial.
  An explicit count is always honoured, however small the input, which is
  what lets a test compare a parallel run with a serial one on a tiny corpus.
"""

from __future__ import annotations

import multiprocessing
import os
from collections import deque
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Any, Callable, Iterable, Iterator, Sequence

from .errors import ValidationError

ENV_JOBS = "RL_JOBS"
#: Upper bound of the automatic worker count: past this the parent's own work
#: (serialising results, writing files) and the memory of the workers cost more
#: than another core returns.
MAX_AUTO_JOBS = 8
#: Below this many items an *automatic* count stays serial; starting a pool for
#: a handful of remotes costs more than the work.
AUTO_MIN_ITEMS = 64
#: Chunks in flight per worker. The window, not the corpus, bounds the results
#: held in the parent.
WINDOW_PER_WORKER = 3

_requested: int | None = None
_in_worker = False


def parse_jobs(text: str, *, source: str) -> int:
    """A worker count from user text, or a ValidationError saying where it came from."""
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise ValidationError(
            f"{source} is {text!r}; give a whole number of worker processes, "
            "1 or more (1 runs everything in this process)"
        )
    return value


def configure(jobs: int | None) -> None:
    """Fix the worker count for this process (the ``--jobs`` flag); None clears it."""
    global _requested
    _requested = jobs


def usable_cpus() -> int:
    """CPUs this process may run on: ``taskset`` and cgroup-cpuset aware."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # not available on macOS or Windows
        return max(1, os.cpu_count() or 1)


def worker_count(items: int | None = None) -> int:
    """How many worker processes a loop over ``items`` items would use."""
    if _in_worker:
        return 1  # a worker never starts a pool of its own
    if _requested is not None:
        return _requested
    env = os.environ.get(ENV_JOBS, "").strip()
    if env:
        return parse_jobs(env, source=f"${ENV_JOBS}")
    if items is not None and items < AUTO_MIN_ITEMS:
        return 1
    return min(usable_cpus(), MAX_AUTO_JOBS)


def _mark_worker() -> None:
    global _in_worker
    _in_worker = True


def _run_chunk(func: Callable[[Any], Any], chunk: Sequence[Any]) -> list[Any]:
    return [func(item) for item in chunk]


def ordered_map(
    func: Callable[[Any], Any],
    items: Iterable[Any],
    *,
    chunk: int | None = None,
) -> Iterator[Any]:
    """``(func(item) for item in items)``, computed by worker processes.

    ``func`` must be a module-level function, or a ``functools.partial`` of
    one, and its arguments and results must pickle. Results arrive in input
    order. An exception raised for an item is re-raised here when that item's
    turn comes, so the *first* failing item in input order is the one reported,
    exactly as in the serial loop; later items may already have run.
    """
    items = list(items)
    jobs = worker_count(len(items))
    if jobs <= 1 or len(items) < 2:
        yield from map(func, items)
        return

    size = chunk or max(1, min(16, len(items) // (jobs * 8)))
    chunks = [items[i:i + size] for i in range(0, len(items), size)]
    # fork starts in milliseconds and shares the already imported modules;
    # where it does not exist (Windows) the platform default is used, and
    # everything shipped to a worker is a module-level function.
    context = (
        multiprocessing.get_context("fork")
        if "fork" in multiprocessing.get_all_start_methods() else None
    )
    window = jobs * WINDOW_PER_WORKER
    pending: deque[Future] = deque()
    submitted = 0
    with ProcessPoolExecutor(
        max_workers=jobs, mp_context=context, initializer=_mark_worker
    ) as pool:
        try:
            while submitted < len(chunks) or pending:
                while submitted < len(chunks) and len(pending) < window:
                    pending.append(pool.submit(_run_chunk, func, chunks[submitted]))
                    submitted += 1
                yield from pending.popleft().result()
        finally:
            # A consumer that stops early, or an item that raised, must not
            # leave the rest of the window running.
            for future in pending:
                future.cancel()
