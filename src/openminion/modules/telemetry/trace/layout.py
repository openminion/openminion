import re
import json
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


def delete_invocation_trace_artifacts(trace_root: Path, *, invocation_id: str) -> int:
    if not trace_root.exists():
        return 0
    removed = 0
    for path in trace_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not _contains_invocation(payload, invocation_id):
            continue
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def _contains_invocation(value: object, invocation_id: str) -> bool:
    if isinstance(value, dict):
        if str(value.get("invocation_id") or "") == invocation_id:
            return True
        return any(_contains_invocation(item, invocation_id) for item in value.values())
    if isinstance(value, list):
        return any(_contains_invocation(item, invocation_id) for item in value)
    return False


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


def write_protected_trace_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


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


__all__ = [
    "build_trace_file_path",
    "delete_invocation_trace_artifacts",
    "resolve_trace_root",
    "write_protected_trace_file",
]
