"""CLI update-available notification helper."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from pathlib import Path
import re
from typing import Callable, Mapping
from urllib.request import urlopen


DEFAULT_UPDATE_CHECK_TTL_SECONDS = 24 * 60 * 60
DEFAULT_UPDATE_CHECK_TIMEOUT_SECONDS = 1.0
OPENMINION_UPDATE_CHECK_ENV = "OPENMINION_UPDATE_CHECK"
OPENMINION_NO_UPDATE_CHECK_ENV = "OPENMINION_NO_UPDATE_CHECK"


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    update_available: bool
    source: str = ""

    def render_notice(self) -> str:
        if not self.update_available:
            return ""
        return (
            f"Update available! {self.current_version} -> {self.latest_version}\n"
            "Run `python -m pip install --upgrade openminion` to update."
        )


def default_update_cache_path(*, data_root: Path | None = None) -> Path:
    root = Path(data_root).expanduser() if data_root is not None else Path.home()
    if data_root is None:
        root = root / ".openminion"
    return root / "update-check.json"


def check_update_available(
    *,
    current_version: str,
    package_name: str = "openminion",
    cache_path: Path,
    env: Mapping[str, str] | None = None,
    now: float | None = None,
    fetcher: Callable[[str, float], str] | None = None,
    timeout_seconds: float = DEFAULT_UPDATE_CHECK_TIMEOUT_SECONDS,
    ttl_seconds: int = DEFAULT_UPDATE_CHECK_TTL_SECONDS,
) -> UpdateCheckResult | None:
    """Return update-check result, or None when disabled/unavailable."""
    if _update_check_disabled(env or {}):
        return None
    current_time = float(time.time() if now is None else now)
    cached = _read_cached_result(
        cache_path=cache_path,
        current_version=current_version,
        now=current_time,
        ttl_seconds=ttl_seconds,
    )
    if cached is not None:
        return cached
    fetch = fetcher or _fetch_latest_version
    try:
        latest = fetch(package_name, timeout_seconds).strip()
    except Exception:
        return None
    if not latest:
        return None
    result = _build_result(current_version=current_version, latest_version=latest)
    _write_cached_result(cache_path=cache_path, result=result, checked_at=current_time)
    return result


def _update_check_disabled(env: Mapping[str, str]) -> bool:
    no_update = str(env.get(OPENMINION_NO_UPDATE_CHECK_ENV, "") or "").strip().lower()
    if no_update in {"1", "true", "yes", "on"}:
        return True
    enabled = str(env.get(OPENMINION_UPDATE_CHECK_ENV, "") or "").strip().lower()
    return enabled in {"0", "false", "no", "off"}


def _fetch_latest_version(package_name: str, timeout_seconds: float) -> str:
    url = f"https://pypi.org/pypi/{package_name}/json"
    with urlopen(url, timeout=float(timeout_seconds)) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    info = payload.get("info", {}) if isinstance(payload, dict) else {}
    return str(info.get("version", "") or "").strip()


def _build_result(*, current_version: str, latest_version: str) -> UpdateCheckResult:
    update_available = False
    current_parts = _version_parts(current_version)
    latest_parts = _version_parts(latest_version)
    if current_parts and latest_parts:
        update_available = latest_parts > current_parts
    return UpdateCheckResult(
        current_version=current_version,
        latest_version=latest_version,
        update_available=update_available,
        source="pypi",
    )


def _version_parts(value: str) -> tuple[int, ...]:
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", str(value or ""))
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _read_cached_result(
    *,
    cache_path: Path,
    current_version: str,
    now: float,
    ttl_seconds: int,
) -> UpdateCheckResult | None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        checked_at = float(payload.get("checked_at", 0) or 0)
    except (TypeError, ValueError):
        return None
    if now - checked_at > ttl_seconds:
        return None
    latest = str(payload.get("latest_version", "") or "").strip()
    if not latest:
        return None
    return _build_result(current_version=current_version, latest_version=latest)


def _write_cached_result(
    *,
    cache_path: Path,
    result: UpdateCheckResult,
    checked_at: float,
) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "checked_at": checked_at,
                    "latest_version": result.latest_version,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except OSError:
        return


__all__ = [
    "DEFAULT_UPDATE_CHECK_TIMEOUT_SECONDS",
    "DEFAULT_UPDATE_CHECK_TTL_SECONDS",
    "OPENMINION_NO_UPDATE_CHECK_ENV",
    "OPENMINION_UPDATE_CHECK_ENV",
    "UpdateCheckResult",
    "check_update_available",
    "default_update_cache_path",
]
