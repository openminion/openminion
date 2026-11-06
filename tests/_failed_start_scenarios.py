"""Checks for what a failed `APIRuntime` start still holds (#447).

The SQLite tests in `tests/api/runtime/test_failed_start_cleanup.py` and the
PostgreSQL tests in `tests/integration/test_runtime_storage_postgres.py` use
these. Automatic garbage collection is off while a test runs, so a resource
counts as released only when its owner closed it, not when a collection
happened to free it.
"""

from __future__ import annotations

import gc
import os
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil

# The threads a start creates: the runtime manager's sweeper (and its agent
# workers), and the pool-health emitter of a pooled record store.
START_THREAD_PREFIXES = ("runtimectl-", "storage-pool-health-emitter")


@contextmanager
def automatic_gc_off() -> Iterator[None]:
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


def start_threads_alive(before: set[threading.Thread]) -> list[str]:
    return sorted(
        thread.name
        for thread in threading.enumerate()
        if thread not in before
        and thread.is_alive()
        and thread.name.startswith(START_THREAD_PREFIXES)
    )


def open_files_under(root: Path) -> list[str]:
    prefix = os.path.normcase(str(root.resolve()))
    held = set()
    for item in psutil.Process().open_files():
        path = os.path.normcase(str(Path(item.path).resolve()))
        if path.startswith(prefix):
            held.add(Path(path).relative_to(prefix).as_posix())
    return sorted(held)


def assert_released(root: Path, threads_before: set[threading.Thread]) -> None:
    """Nothing under root is open and no start thread is alive, right now.

    On Windows an open handle also blocks renaming and deleting a file, so
    there every file under root is renamed at once and the tree is removed.
    """
    assert open_files_under(root) == []
    assert start_threads_alive(threads_before) == []
    if os.name != "nt":
        return
    files = sorted(path for path in root.rglob("*") if path.is_file())
    moved = [path.with_name(path.name + ".moved") for path in files]
    for source, target in zip(files, moved):
        os.replace(source, target)
    for source, target in zip(files, moved):
        os.replace(target, source)
    shutil.rmtree(root)


def connection_is_closed(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False
