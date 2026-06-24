import time
from pathlib import Path
from threading import RLock

_REPO_MAP_CACHE_TTL_S = 60.0
_REPO_MAP_CACHE: dict[tuple[str, str, bool, int], tuple[float, str]] = {}
_CACHE_LOCK = RLock()


def repo_map_cache_get(
    *,
    session_id: str,
    workspace_root: Path,
    include_hidden: bool,
    max_tokens: int,
) -> str | None:
    key = (
        str(session_id or "").strip(),
        str(workspace_root),
        bool(include_hidden),
        max(1, int(max_tokens or 1)),
    )
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _REPO_MAP_CACHE.get(key)
        if cached is None:
            return None
        ts, payload = cached
        if now - ts > _REPO_MAP_CACHE_TTL_S:
            _REPO_MAP_CACHE.pop(key, None)
            return None
        return payload


def repo_map_cache_put(
    *,
    session_id: str,
    workspace_root: Path,
    include_hidden: bool,
    max_tokens: int,
    payload: str,
) -> None:
    key = (
        str(session_id or "").strip(),
        str(workspace_root),
        bool(include_hidden),
        max(1, int(max_tokens or 1)),
    )
    with _CACHE_LOCK:
        _REPO_MAP_CACHE[key] = (time.monotonic(), str(payload or ""))


def invalidate_repo_map_cache(
    *,
    workspace_root: Path | None = None,
    path: str = "",
) -> None:
    normalized_path = str(path or "").strip()
    candidate = Path(normalized_path) if normalized_path else None
    if candidate is not None and workspace_root is not None:
        try:
            relative_path = candidate.relative_to(Path(workspace_root))
        except ValueError:
            relative_path = candidate
    else:
        relative_path = candidate
    relative_token = str(relative_path or "").strip()
    file_name = Path(relative_token).name if relative_token else ""
    if (
        relative_token.startswith("tests/")
        or "/tests/" in relative_token
        or file_name.startswith("test_")
        or file_name.endswith("_test.py")
    ):
        return
    workspace_prefix = str(workspace_root) if workspace_root is not None else ""
    with _CACHE_LOCK:
        for key in list(_REPO_MAP_CACHE.keys()):
            _, cached_workspace, _, _ = key
            if workspace_prefix and cached_workspace != workspace_prefix:
                continue
            _REPO_MAP_CACHE.pop(key, None)


def reset_repo_map_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _REPO_MAP_CACHE.clear()


__all__ = [
    "invalidate_repo_map_cache",
    "repo_map_cache_get",
    "repo_map_cache_put",
    "reset_repo_map_cache_for_tests",
]
