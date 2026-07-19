import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class VectorSyncScheduler:
    """Background scheduler for vector embedding sync.

    Processes pending records in configurable batch size without blocking
    the main turn path.
    """

    def __init__(
        self,
        vector_adapter: Any,
        *,
        interval_seconds: int = 30,
        batch_size: int = 32,
    ):
        self._vector_adapter = vector_adapter
        self._interval = interval_seconds
        self._batch_size = batch_size
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stats = {
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
            self._thread = threading.Thread(
                target=self._run_loop,
                name="vector-sync",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "Vector sync scheduler started: interval=%ds, batch_size=%d",
                self._interval,
                self._batch_size,
            )

    def stop(self) -> None:
        """Stop the background sync thread."""
        with self._lock:
            if not self._running:
                return

            self._running = False
            if self._thread:
                self._thread.join(timeout=5.0)
                self._thread = None

            logger.info("Vector sync scheduler stopped")

    def sync_now(self, *, limit: Optional[int] = None) -> int:
        """Trigger an immediate sync.

        Returns the number of records processed.
        """
        if self._vector_adapter is None:
            return 0

        try:
            limit = limit or self._batch_size
            processed = self._vector_adapter.sync_pending_records(limit=limit)

            with self._lock:
                self._stats["records_processed"] += processed
                self._stats["last_sync_at"] = time.time()

            if processed > 0:
                logger.debug("Vector sync completed: processed=%d", processed)

            return processed
        except Exception as e:
            logger.warning("Vector sync failed: %s", e)
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
            try:
                self.sync_now()
            except Exception as e:
                logger.warning("Vector sync loop error: %s", e)

            time.sleep(self._interval)
