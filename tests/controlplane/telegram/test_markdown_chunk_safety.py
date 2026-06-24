from __future__ import annotations

import pytest

from openminion.modules.controlplane.channels.telegram.delivery import (
    _CODE_FENCE,
    split_text_markdown_aware,
)


def _count_fences(s: str) -> int:
    return s.count(_CODE_FENCE)


def _has_balanced_fences(s: str) -> bool:
    return _count_fences(s) % 2 == 0


def _is_parseable_markdown_v2(chunk: str) -> bool:
    idx = 0
    n = len(chunk)
    stack: list[tuple[str, str | None]] = []
    while idx < n:
        if chunk.startswith("\\", idx):
            idx += 2
            continue
        if chunk.startswith("```", idx):
            marker = "```"
            if stack and stack[-1][0] == marker:
                stack.pop()
            else:
                stack.append((marker, None))
            idx += 3
            continue
        if stack and stack[-1][0] == "```":
            idx += 1
            continue
        if chunk.startswith("[", idx):
            end = chunk.find("](", idx + 1)
            if end == -1:
                return False
            close = chunk.find(")", end + 2)
            if close == -1:
                return False
            idx = close + 1
            continue
        for marker in ("||", "__", "`", "*", "_", "~"):
            if not chunk.startswith(marker, idx):
                continue
            if stack and stack[-1][0] == marker:
                stack.pop()
            else:
                stack.append((marker, None))
            idx += len(marker)
            break
        else:
            idx += 1
    return not stack


def test_long_body_with_fence_at_boundary_splits_outside_fence() -> None:
    fence_body = "x" * 60
    pre = "a" * 4000
    post = "b" * 6000
    body = f"{pre}\n```\n{fence_body}\n```\n{post}"

    chunks = split_text_markdown_aware(body, limit=4096)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 4096
        assert _has_balanced_fences(chunk), (
            f"chunk has unbalanced ``` count={_count_fences(chunk)}: {chunk[:80]!r}..."
        )


def test_multiple_fences_each_chunk_independently_parseable() -> None:
    parts: list[str] = []
    for i in range(3):
        prose = ("p" + str(i)) * 1500
        fence = f"```\nfence-{i}-" + ("c" * 200) + "\n```"
        parts.append(prose)
        parts.append(fence)
    body = "\n\n".join(parts)
    assert len(body) > 9000

    chunks = split_text_markdown_aware(body, limit=4096)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 4096
        assert _has_balanced_fences(chunk)


def test_single_oversized_code_fence_is_split_with_rewrapped_fences() -> None:
    inner = "z" * 5000
    body = f"```\n{inner}\n```"

    chunks = split_text_markdown_aware(body, limit=4096)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert _has_balanced_fences(chunk)
        assert len(chunk) <= 4096
        assert chunk.startswith(_CODE_FENCE)
        assert chunk.endswith(_CODE_FENCE)
    rebuilt = "".join(
        chunk.removeprefix(_CODE_FENCE + "\n").removesuffix("\n" + _CODE_FENCE)
        for chunk in chunks
    )
    assert rebuilt == inner


def test_short_body_with_fence_returns_single_chunk() -> None:
    body = "hello\n```\nint x = 1;\n```\nworld"
    chunks = split_text_markdown_aware(body, limit=4096)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert "int x = 1;" in chunk
    assert "```\nint x = 1;\n```" in chunk
    assert chunk.startswith("hello")
    assert chunk.endswith("world")


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("bold", "start " + "*" + ("b" * 5000) + "* end"),
        ("italic", "start " + "_" + ("i" * 5000) + "_ end"),
        ("underline", "start " + "__" + ("u" * 5000) + "__ end"),
        ("strikethrough", "start " + "~" + ("s" * 5000) + "~ end"),
        ("spoiler", "start " + "||" + ("p" * 5000) + "|| end"),
        ("inline_code", "start " + "`" + ("c" * 5000) + "` end"),
        ("link", "start [" + ("l" * 5000) + "](https://example.com/path) end"),
    ],
)
def test_inline_markdown_entities_split_on_parseable_boundaries(
    label: str, body: str
) -> None:
    del label
    chunks = split_text_markdown_aware(body, limit=4096)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 4096
        assert _is_parseable_markdown_v2(chunk), chunk
