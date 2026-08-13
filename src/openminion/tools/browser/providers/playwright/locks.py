import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager


class BrowserTabLockedError(RuntimeError):
    def __init__(self, tab_id: str) -> None:
        super().__init__(f"tool.browser.locked: {tab_id}")
        self.tab_id = tab_id


class PlaywrightLockManager:
    def __init__(self, *, default_ttl_s: int = 300) -> None:
        self._default_ttl_s = max(1, int(default_ttl_s))
        self._locks: dict[str, threading.RLock] = {}
        self._manual_expiry: dict[str, float] = {}
        self._guard = threading.Lock()

    def lock(self, key: str, *, ttl_s: int | None = None) -> bool:
        token = str(key).strip()
        if not token:
            raise ValueError("lock key is required")
        self._expire_manual_locks()
        lock = self._get_lock(token)
        if not lock.acquire(blocking=False):
            return False
        expiry = time.monotonic() + float(ttl_s or self._default_ttl_s)
        with self._guard:
            self._manual_expiry[token] = expiry
        return True

    def unlock(self, key: str) -> bool:
        token = str(key).strip()
        if not token:
            return False
        lock = self._get_lock(token)
        with self._guard:
            self._manual_expiry.pop(token, None)
        try:
            lock.release()
            return True
        except RuntimeError:
            return False

    @contextmanager
    def action_lock(self, key: str) -> Iterator[None]:
        token = str(key).strip()
        if not token:
            raise ValueError("lock key is required")
        self._expire_manual_locks()
        lock = self._get_lock(token)
        if not lock.acquire(blocking=False):
            raise BrowserTabLockedError(token)
        try:
            yield
        finally:
            lock.release()

    def _get_lock(self, key: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def _expire_manual_locks(self) -> None:
        now = time.monotonic()
        with self._guard:
            expired = [
                key for key, expiry in self._manual_expiry.items() if expiry <= now
            ]
            for key in expired:
                self._manual_expiry.pop(key, None)

        for key in expired:
            lock = self._get_lock(key)
            try:
                lock.release()
            except RuntimeError:
                # Lock was not held by the current thread; it will clear on explicit unlock.
                pass
