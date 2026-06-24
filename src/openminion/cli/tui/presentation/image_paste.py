from __future__ import annotations

from pathlib import Path


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_GIF_SIGNATURES = (b"GIF87a", b"GIF89a")
_WEBP_PREFIX = b"RIFF"
_WEBP_TAG = b"WEBP"
_BMP_SIGNATURE = b"BM"


def detect_image_path(text: str, *, working_dir: Path | None = None) -> Path | None:
    raw = (text or "").strip()
    if not raw or "\n" in raw:
        return None
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        raw = raw[1:-1]
    if not raw:
        return None
    lower = raw.lower()
    if not any(lower.endswith(ext) for ext in _IMAGE_EXTENSIONS):
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() and working_dir is not None:
        candidate = Path(working_dir) / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_file():
        return None
    return resolved


def format_image_reference(path: Path, *, working_dir: Path | None = None) -> str:
    resolved = Path(path)
    if working_dir is not None:
        try:
            rel = resolved.relative_to(Path(working_dir).resolve(strict=False))
            return f"[image: {rel.as_posix()}]"
        except ValueError:
            pass
    return f"[image: {resolved.as_posix()}]"


def detect_image_bytes(data: bytes) -> str | None:
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    head = bytes(data[:16])
    if head.startswith(_PNG_SIGNATURE):
        return "png"
    if head.startswith(_JPEG_SIGNATURE):
        return "jpeg"
    if any(head.startswith(sig) for sig in _GIF_SIGNATURES):
        return "gif"
    if head.startswith(_WEBP_PREFIX) and len(data) >= 12 and data[8:12] == _WEBP_TAG:
        return "webp"
    if head.startswith(_BMP_SIGNATURE):
        return "bmp"
    return None


def store_image_bytes(
    data: bytes,
    *,
    data_root: Path,
    session_id: str,
    turn_index: int,
    format_hint: str | None = None,
) -> Path:
    fmt = format_hint or detect_image_bytes(data)
    if fmt is None:
        raise ValueError("data does not look like a recognized image format")
    images_dir = Path(data_root) / "images" / str(session_id)
    images_dir.mkdir(parents=True, exist_ok=True)
    target = images_dir / f"{int(turn_index)}.{fmt}"
    target.write_bytes(bytes(data))
    return target


__all__ = [
    "detect_image_bytes",
    "detect_image_path",
    "format_image_reference",
    "store_image_bytes",
]
