import pytest

from openminion.modules.storage.runtime.pinned_context import (
    PinnedContextEntry,
    PinnedContextPolicy,
    decode_pinned_context,
    encode_pinned_context,
    normalize_pin_entries,
    render_pinned_context,
)


def test_encode_decode_round_trip() -> None:
    encoded = encode_pinned_context(
        [
            PinnedContextEntry(
                pin_id="p1", source="user", text="Remember timezone PST"
            ),
            PinnedContextEntry(
                pin_id="p2", source="policy", text="Do not leak secrets"
            ),
        ]
    )
    decoded = decode_pinned_context(encoded)
    assert len(decoded) == 2
    assert decoded[0].source == "user"
    assert decoded[1].source == "policy"


def test_invalid_source_rejected() -> None:
    with pytest.raises(ValueError):
        encode_pinned_context(
            [PinnedContextEntry(pin_id="bad", source="unknown", text="x")]
        )


def test_limits_enforced() -> None:
    with pytest.raises(ValueError):
        normalize_pin_entries(
            [
                PinnedContextEntry(pin_id=f"p{i}", source="system", text=f"pin-{i}")
                for i in range(13)
            ],
            policy=PinnedContextPolicy(
                max_pins=12, max_chars_per_pin=20, max_total_chars=500
            ),
        )


def test_legacy_plain_text_fails_open() -> None:
    decoded = decode_pinned_context("Legacy pinned text")
    assert len(decoded) == 1
    assert decoded[0].source == "system"
    assert render_pinned_context("Legacy pinned text") == "Legacy pinned text"


def test_render_structured_pins_is_deterministic() -> None:
    encoded = encode_pinned_context(
        [
            PinnedContextEntry(
                pin_id="p1", source="operator", text="Use concise output"
            ),
            PinnedContextEntry(
                pin_id="p2", source="user", text="Project codename is Atlas"
            ),
        ]
    )
    rendered = render_pinned_context(encoded)
    assert (
        rendered
        == "- [operator] Use concise output\n- [user] Project codename is Atlas"
    )
