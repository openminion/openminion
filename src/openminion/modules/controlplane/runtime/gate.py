import logging
from typing import Final


_LOG = logging.getLogger(__name__)


class ControlPlaneLegacyBlockedError(RuntimeError):
    """Raised when required controlplane modules are unavailable."""


def _resolve_modules_only() -> bool:
    try:
        from openminion.modules.tool import _MODULES_ONLY as _tool_modules_only
    except Exception:  # pragma: no cover - defensive
        return True
    return bool(_tool_modules_only)


_MODULES_ONLY_CACHED: Final[bool] = _resolve_modules_only()


def assert_controlplane_lane(
    *,
    ingress: str,
    required_modules: tuple[str, ...] = (),
) -> None:
    if not _MODULES_ONLY_CACHED:  # pragma: no cover - hardcoded True
        return

    if not required_modules:
        return

    failures: list[str] = []
    for module_path in required_modules:
        try:
            __import__(module_path)
        except ImportError as exc:
            failures.append(f"'{module_path}': {exc}")

    if failures:
        joined = "; ".join(failures)
        message = (
            f"controlplane[{ingress}]: legacy_blocked: missing module "
            f"dependency {joined}"
        )
        _LOG.error(message)
        raise ControlPlaneLegacyBlockedError(message)


TELEGRAM_INGRESS_REQUIRED_MODULES: Final[tuple[str, ...]] = (
    "openminion.modules.controlplane.runtime.dispatcher",
    "openminion.modules.controlplane.runtime.router",
    "openminion.modules.controlplane.channels.telegram.normalization",
)


__all__ = [
    "ControlPlaneLegacyBlockedError",
    "TELEGRAM_INGRESS_REQUIRED_MODULES",
    "assert_controlplane_lane",
]
