import re
from pathlib import Path

from openminion.base.config.env import resolve_environment_config
from openminion.base.config.paths import resolve_data_root, resolve_home_root

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TURN_TS_RE = re.compile(r"_(\d{10,})$")


def resolve_trace_root(*, home_root: Path | None) -> Path:
    env_owner = resolve_environment_config()
    trace_dir_env = str(env_owner.openminion_trace_requests_dir or "").strip()
    if trace_dir_env:
        return Path(trace_dir_env).expanduser().resolve(strict=False)
    resolved_home = (
        Path(home_root).expanduser().resolve(strict=False)
        if home_root is not None
        else resolve_home_root(
            config_path=None,
            fallback=str(Path.cwd()),
            env=env_owner,
        ).resolve(strict=False)
    )
    data_root = resolve_data_root(
        resolved_home,
        data_root=str(env_owner.openminion_data_root or "").strip() or None,
        env=env_owner,
    ).resolve(strict=False)
    return data_root / "traces"


def build_trace_file_path(
    trace_root: Path,
    *,
    session_id: str,
    turn_id: str,
    inference_step: int,
    label: str,
    suffix: str,
) -> tuple[Path, str]:
    agent_id = _extract_agent_id(session_id)
    session_slug = _extract_session_slug(session_id)
    run_key = _extract_run_key(turn_id)
    run_dir = trace_root / "llm" / agent_id / f"{run_key}-{session_slug}"
    filename = f"step{max(0, int(inference_step)):02d}-{label}{suffix}"
    path = run_dir / filename
    relative = str(path.relative_to(trace_root))
    return path, relative


def _extract_agent_id(session_id: str) -> str:
    token = str(session_id or "").strip()
    if "::" in token:
        token = token.split("::", 1)[0]
    return _safe_segment(token, fallback="agent")


def _extract_session_slug(session_id: str) -> str:
    token = str(session_id or "").strip()
    if "::" in token:
        token = token.split("::", 1)[1]
    return _safe_segment(token, fallback="session")


def _extract_run_key(turn_id: str) -> str:
    token = str(turn_id or "").strip()
    if token:
        match = _TURN_TS_RE.search(token)
        if match:
            return _safe_segment(match.group(1), fallback="turn")
        return _safe_segment(token, fallback="turn")
    return "turn"


def _safe_segment(value: str, *, fallback: str) -> str:
    token = str(value or "").strip()
    if not token:
        return fallback
    normalized = _SAFE_SEGMENT_RE.sub("-", token).strip("-._")
    return normalized or fallback


__all__ = ["resolve_trace_root", "build_trace_file_path"]
