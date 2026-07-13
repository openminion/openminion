from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ARG_PLACEHOLDER_RE = re.compile(r"\$(\d+|ARGUMENTS)")
_AT_FILE_RE = re.compile(r"@([^\s@!`]+)")
_BANG_CMD_RE = re.compile(r"!`([^`]+)`")
_VALID_SLASH_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class CustomCommand:
    """A discovered custom slash command."""

    slash: str
    body: str
    source: str
    path: Path
    description: str = ""
    model: str = ""
    agent: str = ""
    frontmatter: dict[str, str] = field(default_factory=dict)


def discover_custom_commands(
    *,
    project_dir: Path | None,
    user_dir: Path | None,
) -> dict[str, CustomCommand]:
    return discover_with_warnings(project_dir=project_dir, user_dir=user_dir)[0]


def discover_with_warnings(
    *,
    project_dir: Path | None,
    user_dir: Path | None,
) -> tuple[dict[str, CustomCommand], list[str]]:
    warnings: list[str] = []
    result: dict[str, CustomCommand] = {}

    for source, base in (("user", user_dir), ("project", project_dir)):
        if base is None or not Path(base).is_dir():
            continue
        for md_path in sorted(Path(base).glob("*.md")):
            try:
                cmd = _load_command(md_path, source=source)
            except _CustomCommandError as exc:
                warnings.append(f"{md_path}: {exc}")
                continue
            result[cmd.slash] = cmd
    return result, warnings


def render_command(
    cmd: CustomCommand,
    *,
    arg_string: str,
    working_dir: Path | None = None,
) -> str:
    args = shlex.split(arg_string) if arg_string.strip() else []

    def _arg_sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "ARGUMENTS":
            return arg_string
        try:
            idx = int(key) - 1
        except ValueError:
            return match.group(0)
        if 0 <= idx < len(args):
            return args[idx]
        return ""

    body = _ARG_PLACEHOLDER_RE.sub(_arg_sub, cmd.body)

    def _file_sub(match: re.Match[str]) -> str:
        rel = match.group(1)
        base = Path(working_dir) if working_dir is not None else Path.cwd()
        candidate = (base / rel).expanduser()
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return match.group(0)
        if not resolved.is_file():
            return match.group(0)
        try:
            return resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return match.group(0)

    body = _AT_FILE_RE.sub(_file_sub, body)

    def _cmd_sub(match: re.Match[str]) -> str:
        shell_cmd = match.group(1)
        try:
            completed = subprocess.run(
                shell_cmd,
                shell=True,
                capture_output=True,
                timeout=5,
                check=False,
                cwd=working_dir,
            )
        except subprocess.TimeoutExpired:
            return f"[!{shell_cmd}: timed out]"
        except (OSError, subprocess.SubprocessError) as exc:
            return f"[!{shell_cmd}: {exc}]"
        out = (completed.stdout or b"").decode("utf-8", errors="replace").rstrip()
        return out

    body = _BANG_CMD_RE.sub(_cmd_sub, body)
    return body


class _CustomCommandError(ValueError):
    pass


def _load_command(path: Path, *, source: str) -> CustomCommand:
    name = path.stem.lower()
    if not _VALID_SLASH_NAME.match(name):
        raise _CustomCommandError(
            f"invalid slash name {name!r} — must match [a-z][a-z0-9_-]*"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _CustomCommandError(f"unreadable: {exc}") from exc

    frontmatter: dict[str, str] = {}
    body = raw
    match = _FRONTMATTER_RE.match(raw)
    if match is not None:
        frontmatter = _parse_minimal_yaml(match.group(1))
        body = raw[match.end() :]
    return CustomCommand(
        slash=f"/{name}",
        body=body,
        source=source,
        path=path,
        description=frontmatter.get("description", "").strip(),
        model=frontmatter.get("model", "").strip(),
        agent=frontmatter.get("agent", "").strip(),
        frontmatter=frontmatter,
    )


def _parse_minimal_yaml(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


__all__ = [
    "CustomCommand",
    "discover_custom_commands",
    "discover_with_warnings",
    "render_command",
]
