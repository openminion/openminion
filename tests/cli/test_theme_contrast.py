from __future__ import annotations

import pytest

from openminion.cli.theme import DARK, LIGHT, SHIPPED_THEMES, Theme


# ── WCAG ratio implementation ────────────────────────────────────────────────


def _channel_to_linear(channel_byte: int) -> float:
    c = channel_byte / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected #rrggbb, got {hex_color!r}")
    r = _channel_to_linear(int(h[0:2], 16))
    g = _channel_to_linear(int(h[2:4], 16))
    b = _channel_to_linear(int(h[4:6], 16))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(fg_hex)
    l2 = _relative_luminance(bg_hex)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ── Spec sanity check on the formula itself ──────────────────────────────────


def test_contrast_formula_known_pairs() -> None:
    assert _contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, rel=1e-3)
    assert _contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, rel=1e-3)
    # Mid-grey on white roughly 5.7:1 (sanity, not pinned exact).
    ratio = _contrast_ratio("#767676", "#ffffff")
    assert 4.0 < ratio < 6.0


# ── Chat-message pairs: body text floor (4.5:1) ──────────────────────────────


_BODY_TEXT_FLOOR = 4.5


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["LIGHT", "DARK"])
def test_chat_message_pairs_meet_body_text_contrast(theme: Theme) -> None:
    failures: list[str] = []
    for pair_name, fg, bg in theme.color_pairs():
        ratio = _contrast_ratio(fg, bg)
        if ratio < _BODY_TEXT_FLOOR:
            failures.append(
                f"{theme.name}.{pair_name}: {fg} on {bg} = {ratio:.2f}:1 "
                f"(<{_BODY_TEXT_FLOOR})"
            )
    assert not failures, (
        f"chat-message pairs below {_BODY_TEXT_FLOOR}:1 floor:\n  "
        + "\n  ".join(failures)
    )


# ── Text-on-surface pairs ────────────────────────────────────────────────────


def _text_on_surface_pairs(theme: Theme) -> list[tuple[str, str, str]]:
    return [
        ("text_primary on surface_app", theme.text_primary, theme.surface_app_bg),
        ("text_primary on surface_panel", theme.text_primary, theme.surface_panel_bg),
        ("text_secondary on surface_app", theme.text_secondary, theme.surface_app_bg),
        (
            "text_secondary on surface_panel",
            theme.text_secondary,
            theme.surface_panel_bg,
        ),
    ]


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["LIGHT", "DARK"])
def test_text_on_surface_meets_body_text_contrast(theme: Theme) -> None:
    failures: list[str] = []
    for label, fg, bg in _text_on_surface_pairs(theme):
        ratio = _contrast_ratio(fg, bg)
        if ratio < _BODY_TEXT_FLOOR:
            failures.append(
                f"{theme.name}.{label}: {fg} on {bg} = {ratio:.2f}:1 "
                f"(<{_BODY_TEXT_FLOOR})"
            )
    assert not failures, (
        f"text-on-surface pairs below {_BODY_TEXT_FLOOR}:1 floor:\n  "
        + "\n  ".join(failures)
    )


# ── State-token pairs (large/bold text floor 3:1) ────────────────────────────


_LARGE_TEXT_FLOOR = 3.0


def _state_pairs(theme: Theme) -> list[tuple[str, str, str]]:
    return [
        ("state_ok on panel", theme.state_ok, theme.surface_panel_bg),
        ("state_warning on panel", theme.state_warning, theme.surface_panel_bg),
        ("state_error on panel", theme.state_error, theme.surface_panel_bg),
        ("state_highlight on panel", theme.state_highlight, theme.surface_panel_bg),
    ]


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["LIGHT", "DARK"])
def test_state_tokens_meet_large_text_contrast(theme: Theme) -> None:
    failures: list[str] = []
    for label, fg, bg in _state_pairs(theme):
        ratio = _contrast_ratio(fg, bg)
        if ratio < _LARGE_TEXT_FLOOR:
            failures.append(
                f"{theme.name}.{label}: {fg} on {bg} = {ratio:.2f}:1 "
                f"(<{_LARGE_TEXT_FLOOR})"
            )
    assert not failures, (
        f"state pairs below {_LARGE_TEXT_FLOOR}:1 floor:\n  " + "\n  ".join(failures)
    )


# ── Future-theme barrier ─────────────────────────────────────────────────────


def test_future_themes_inherit_the_same_gates() -> None:
    body_failures: list[str] = []
    large_failures: list[str] = []
    for name, theme in SHIPPED_THEMES.items():
        for pair_name, fg, bg in theme.color_pairs():
            ratio = _contrast_ratio(fg, bg)
            if ratio < _BODY_TEXT_FLOOR:
                body_failures.append(
                    f"{name}.{pair_name}: {ratio:.2f}:1 (<{_BODY_TEXT_FLOOR})"
                )
        for label, fg, bg in _state_pairs(theme):
            ratio = _contrast_ratio(fg, bg)
            if ratio < _LARGE_TEXT_FLOOR:
                large_failures.append(
                    f"{name}.{label}: {ratio:.2f}:1 (<{_LARGE_TEXT_FLOOR})"
                )
    assert not body_failures, "shipped theme(s) below body floor: " + ", ".join(
        body_failures
    )
    assert not large_failures, "shipped theme(s) below large/bold floor: " + ", ".join(
        large_failures
    )
