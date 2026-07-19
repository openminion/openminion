from __future__ import annotations

import re
from dataclasses import dataclass

from rich.cells import cell_len

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAX_INTERVAL_MS = 2_000
MAX_INLINE_CELLS = 8


class AnimationSpecError(ValueError):
    """Raised when provider frame data is unsafe or malformed."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class AnimationDiagnostic:
    reason: str
    provider_id: str = ""
    name: str = ""
    detail: str = ""

    def render(self) -> str:
        target = (
            f"{self.provider_id}:{self.name}"
            if self.provider_id and self.name
            else self.provider_id or self.name or "animation"
        )
        return f"{target}: {self.reason}" + (f" ({self.detail})" if self.detail else "")


@dataclass(frozen=True)
class AnimationSpec:
    provider_id: str
    name: str
    frames: tuple[str, ...]
    interval_ms: int

    @property
    def cell_width(self) -> int:
        return cell_len(self.frames[0]) if self.frames else 0


@dataclass(frozen=True)
class AnimationResolution:
    spec: AnimationSpec
    source: str
    fallback_reason: str = ""
    diagnostic: AnimationDiagnostic | None = None

    @property
    def is_fallback(self) -> bool:
        return bool(self.fallback_reason)


def validate_animation_spec(spec: AnimationSpec) -> AnimationSpec:
    provider_id = _validate_identifier("provider_id", spec.provider_id)
    name = _validate_identifier("name", spec.name)
    frames = tuple(str(frame) for frame in spec.frames)
    if not frames:
        raise AnimationSpecError("empty_frames", "animation must include frames")
    for frame in frames:
        _validate_frame(frame)
    interval_ms = _validate_interval(spec.interval_ms)
    widths = {cell_len(frame) for frame in frames}
    if len(widths) != 1:
        raise AnimationSpecError(
            "unstable_frame_width",
            f"frames have terminal widths {sorted(widths)}",
        )
    width = next(iter(widths))
    if width <= 0:
        raise AnimationSpecError("empty_frame_width", "frames must occupy cells")
    if width > MAX_INLINE_CELLS:
        raise AnimationSpecError(
            "frame_too_wide",
            f"frame width {width} exceeds {MAX_INLINE_CELLS} cells",
        )
    return AnimationSpec(
        provider_id=provider_id,
        name=name,
        frames=frames,
        interval_ms=interval_ms,
    )


def coerce_animation_spec(raw: object, *, provider_id: str = "") -> AnimationSpec:
    raw_provider = str(getattr(raw, "provider_id", provider_id) or provider_id)
    raw_name = str(getattr(raw, "name", "") or "")
    frames_obj = getattr(raw, "frames", ())
    raw_interval = getattr(raw, "interval_ms", getattr(raw, "interval", 0))
    try:
        frames = tuple(str(frame) for frame in frames_obj)
    except TypeError as exc:
        raise AnimationSpecError("invalid_frames", "frames must be iterable") from exc
    return validate_animation_spec(
        AnimationSpec(
            provider_id=raw_provider,
            name=raw_name,
            frames=frames,
            interval_ms=_validate_interval(raw_interval),
        )
    )


def _validate_identifier(field: str, value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise AnimationSpecError(f"empty_{field}", f"{field} is required")
    if not _IDENTIFIER_RE.match(normalized):
        raise AnimationSpecError(
            f"invalid_{field}",
            f"{field} must be lowercase letters, numbers, dashes, or underscores",
        )
    return normalized


def _validate_interval(value: object) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise AnimationSpecError("invalid_interval", "interval_ms must be an integer") from exc
    if interval <= 0:
        raise AnimationSpecError("invalid_interval", "interval_ms must be positive")
    if interval > MAX_INTERVAL_MS:
        raise AnimationSpecError(
            "invalid_interval",
            f"interval_ms must be <= {MAX_INTERVAL_MS}",
        )
    return interval


def _validate_frame(frame: str) -> None:
    if not frame:
        raise AnimationSpecError("empty_frame", "frame strings must be nonempty")
    if _ANSI_RE.search(frame):
        raise AnimationSpecError("ansi_frame", "frame strings must not contain ANSI")
    if any(char in {"\r", "\n", "\t", "\x1b"} for char in frame):
        raise AnimationSpecError(
            "control_frame",
            "frame strings must not contain terminal controls",
        )
