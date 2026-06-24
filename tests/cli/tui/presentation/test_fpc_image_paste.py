from __future__ import annotations

from pathlib import Path

import pytest

from openminion.cli.tui.presentation.image_paste import (
    detect_image_bytes,
    detect_image_path,
    format_image_reference,
    store_image_bytes,
)


# ── detect_image_path ─────────────────────────────────────────────


def test_detect_image_path_returns_resolved_for_existing_png(tmp_path: Path) -> None:
    image = tmp_path / "screenshot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    assert detect_image_path(str(image)) == image.resolve()


def test_detect_image_path_handles_jpeg_extension(tmp_path: Path) -> None:
    image = tmp_path / "photo.jpeg"
    image.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
    assert detect_image_path(str(image)) == image.resolve()


def test_detect_image_path_returns_none_for_text_paste() -> None:
    assert detect_image_path("hello world") is None


def test_detect_image_path_returns_none_for_multiline_paste(tmp_path: Path) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert detect_image_path(f"{image}\nsecond line") is None


def test_detect_image_path_returns_none_for_nonexistent_file() -> None:
    assert detect_image_path("/nonexistent/path/to/image.png") is None


def test_detect_image_path_rejects_non_image_extension(tmp_path: Path) -> None:
    text_file = tmp_path / "doc.txt"
    text_file.write_text("hello")
    assert detect_image_path(str(text_file)) is None


def test_detect_image_path_strips_double_quotes(tmp_path: Path) -> None:
    image = tmp_path / "with space.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert detect_image_path(f'"{image}"') == image.resolve()


def test_detect_image_path_strips_single_quotes(tmp_path: Path) -> None:
    image = tmp_path / "with space.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert detect_image_path(f"'{image}'") == image.resolve()


def test_detect_image_path_resolves_relative_against_working_dir(
    tmp_path: Path,
) -> None:
    image = tmp_path / "rel.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert detect_image_path("rel.png", working_dir=tmp_path) == image.resolve()


def test_detect_image_path_expands_tilde(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    image = tmp_path / "home.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert detect_image_path("~/home.png") == image.resolve()


def test_detect_image_path_empty_returns_none() -> None:
    assert detect_image_path("") is None
    assert detect_image_path("   ") is None


# ── format_image_reference ───────────────────────────────────────


def test_format_image_reference_uses_relative_when_inside_working_dir(
    tmp_path: Path,
) -> None:
    image = tmp_path / "sub" / "x.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG")
    ref = format_image_reference(image.resolve(), working_dir=tmp_path)
    assert ref == "[image: sub/x.png]"


def test_format_image_reference_falls_back_to_absolute_when_outside_working_dir(
    tmp_path: Path,
) -> None:
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG")
    ref = format_image_reference(outside.resolve(), working_dir=inside)
    assert ref == f"[image: {outside.resolve().as_posix()}]"


def test_format_image_reference_no_working_dir_uses_absolute(tmp_path: Path) -> None:
    image = tmp_path / "abs.png"
    image.write_bytes(b"\x89PNG")
    ref = format_image_reference(image.resolve())
    assert ref == f"[image: {image.resolve().as_posix()}]"


# ── detect_image_bytes (magic-byte sniffing) ─────────────────────


def test_detect_image_bytes_png() -> None:
    assert detect_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32) == "png"


def test_detect_image_bytes_jpeg() -> None:
    assert detect_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32) == "jpeg"


def test_detect_image_bytes_gif87a() -> None:
    assert detect_image_bytes(b"GIF87a" + b"\x00" * 32) == "gif"


def test_detect_image_bytes_gif89a() -> None:
    assert detect_image_bytes(b"GIF89a" + b"\x00" * 32) == "gif"


def test_detect_image_bytes_webp() -> None:
    data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 20
    assert detect_image_bytes(data) == "webp"


def test_detect_image_bytes_bmp() -> None:
    assert detect_image_bytes(b"BM" + b"\x00" * 32) == "bmp"


def test_detect_image_bytes_unrecognized_returns_none() -> None:
    assert detect_image_bytes(b"plain text bytes here xxxxxxxxxxxxxxx") is None


def test_detect_image_bytes_empty_returns_none() -> None:
    assert detect_image_bytes(b"") is None


def test_detect_image_bytes_non_bytes_returns_none() -> None:
    assert detect_image_bytes("string instead of bytes") is None  # type: ignore[arg-type]


# ── store_image_bytes ─────────────────────────────────────────────


def test_store_image_bytes_writes_tempfile_with_correct_extension(
    tmp_path: Path,
) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    target = store_image_bytes(
        png, data_root=tmp_path, session_id="sess-1", turn_index=3
    )
    assert target.exists()
    assert target.suffix == ".png"
    assert target.name == "3.png"
    assert target.parent == tmp_path / "images" / "sess-1"


def test_store_image_bytes_uses_format_hint_when_detection_fails(
    tmp_path: Path,
) -> None:
    target = store_image_bytes(
        b"arbitrary bytes",
        data_root=tmp_path,
        session_id="sess-2",
        turn_index=1,
        format_hint="png",
    )
    assert target.name == "1.png"


def test_store_image_bytes_raises_on_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not look like a recognized image format"):
        store_image_bytes(
            b"not-an-image",
            data_root=tmp_path,
            session_id="sess-3",
            turn_index=1,
        )


def test_store_image_bytes_creates_nested_directories(tmp_path: Path) -> None:
    nested = tmp_path / "deeply" / "nested" / "data"
    png = b"\x89PNG\r\n\x1a\n"
    target = store_image_bytes(png, data_root=nested, session_id="sess-x", turn_index=0)
    assert target.exists()
    assert (nested / "images" / "sess-x").is_dir()


# ── Composer integration (terminal-flow) ─────────────────────────


def test_terminal_flow_composer_converts_pasted_path_to_reference(
    tmp_path: Path,
) -> None:
    from openminion.cli.tui.terminal.composer import TerminalComposer

    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    composer = TerminalComposer(working_dir=str(tmp_path))

    class _FakeBuffer:
        def __init__(self) -> None:
            self.inserted: list[str] = []

        def insert_text(self, text: str) -> None:
            self.inserted.append(text)

    buf = _FakeBuffer()
    composer._apply_pasted_text(str(image), buffer=buf)
    assert buf.inserted == ["[image: shot.png]"]


def test_terminal_flow_composer_preserves_normal_text_paste(tmp_path: Path) -> None:
    from openminion.cli.tui.terminal.composer import TerminalComposer

    composer = TerminalComposer(working_dir=str(tmp_path))

    class _FakeBuffer:
        def __init__(self) -> None:
            self.inserted: list[str] = []

        def insert_text(self, text: str) -> None:
            self.inserted.append(text)

    buf = _FakeBuffer()
    composer._apply_pasted_text("hello world", buffer=buf)
    assert buf.inserted == ["hello world"]
