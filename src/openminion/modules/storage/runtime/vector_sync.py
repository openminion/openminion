import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class VectorSyncScheduler:
    """Run vector embedding sync in a background thread."""

    def __init__(
        self,
        vector_adapter: Any,
        *,
        interval_seconds: int = 30,
    ):
        self._vector_adapter = vector_adapter
        self._interval = interval_seconds
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stats: dict[str, Any] = {
            "records_processed": 0,
            "failures": 0,
            "last_sync_at": None,
        }

    def start(self) -> None:
        """Start the background sync thread."""
        with self._lock:
            if self._running:
                return

            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="vector-sync",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "Vector sync scheduler started: interval=%ds",
                self._interval,
            )

    def stop(self) -> None:
        """Stop the background sync thread."""
        with self._lock:
            if not self._running:
                return

            self._running = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None

        if thread:
            thread.join(timeout=5.0)
        logger.info("Vector sync scheduler stopped")

    def bind_record_source(self, record_source: Any) -> None:
        self._vector_adapter.bind_record_source(record_source)

    def is_alive(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def sync_now(self) -> int:
        """Sync pending records and return the number processed."""
        if self._vector_adapter is None:
            return 0

        try:
            processed = int(self._vector_adapter.sync_pending_records())

            with self._lock:
                self._stats["records_processed"] += processed
                self._stats["last_sync_at"] = time.time()

            if processed > 0:
                logger.debug("Vector sync completed: processed=%d", processed)

            return processed
        except Exception as exc:
            logger.warning("Vector sync failed: %s", exc)
            with self._lock:
                self._stats["failures"] += 1
            return 0

    def get_stats(self) -> dict[str, Any]:
        """Get sync statistics."""
        with self._lock:
            return dict(self._stats)

    def _run_loop(self) -> None:
        """Main sync loop running in background thread."""
        while self._running:
            self.sync_now()
            self._stop_event.wait(self._interval)
