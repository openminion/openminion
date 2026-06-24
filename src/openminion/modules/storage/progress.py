import sys
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProgressReporter(Protocol):
    """Minimal protocol for reporting progress on a long-running task."""

    def on_start(self, *, total: int | None, label: str) -> None: ...

    def on_progress(self, *, advance: int = 1, message: str | None = None) -> None: ...

    def on_end(self, *, success: bool, message: str | None = None) -> None: ...


class NullProgressReporter:
    """Reporter that does nothing. The default for CI and non-TTY callers."""

    def on_start(self, *, total: int | None, label: str) -> None:  # noqa: D401
        pass

    def on_progress(self, *, advance: int = 1, message: str | None = None) -> None:  # noqa: D401
        pass

    def on_end(self, *, success: bool, message: str | None = None) -> None:  # noqa: D401
        pass


class TqdmProgressReporter:
    """Reporter that renders progress through ``tqdm`` when available."""

    def __init__(self, *, force: bool = False) -> None:
        self._force = bool(force)
        self._bar: Any = None
        self._available = False
        try:
            import tqdm  # noqa: F401

            self._available = True
        except Exception:  # noqa: BLE001
            self._available = False

    def _is_active(self) -> bool:
        if not self._available:
            return False
        if self._force:
            return True
        try:
            return bool(sys.stdout.isatty())
        except Exception:  # noqa: BLE001
            return False

    def on_start(self, *, total: int | None, label: str) -> None:
        if not self._is_active():
            return
        try:
            from tqdm import tqdm

            self._bar = tqdm(total=total, desc=str(label), unit="row", leave=False)
        except Exception:  # noqa: BLE001
            self._bar = None

    def on_progress(self, *, advance: int = 1, message: str | None = None) -> None:
        bar = self._bar
        if bar is None:
            return
        try:
            if message is not None:
                bar.set_postfix_str(str(message), refresh=False)
            bar.update(max(0, int(advance)))
        except Exception:  # noqa: BLE001
            return

    def on_end(self, *, success: bool, message: str | None = None) -> None:
        bar = self._bar
        if bar is None:
            return
        try:
            if message is not None:
                bar.set_postfix_str(str(message), refresh=False)
            bar.close()
        except Exception:  # noqa: BLE001
            return
        finally:
            self._bar = None


def select_default_reporter(*, force: bool = False) -> ProgressReporter:
    """Return ``TqdmProgressReporter`` when tqdm+TTY is available, else null."""

    candidate = TqdmProgressReporter(force=force)
    if candidate._is_active():  # noqa: SLF001 — intentional internal check
        return candidate
    return NullProgressReporter()


__all__ = (
    "NullProgressReporter",
    "ProgressReporter",
    "TqdmProgressReporter",
    "select_default_reporter",
)
