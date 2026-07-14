from __future__ import annotations

from argparse import Namespace

import pytest

from openminion.cli.presentation.animation import (
    BUILTIN_ANIMATION_NAME,
    BUILTIN_PROVIDER_ID,
    AnimationRegistry,
    AnimationSelectionError,
    AnimationSpec,
    AnimationSpecError,
    BuiltInAnimationProvider,
    parse_animation_token,
    resolve_focus_animation,
)
from openminion.cli.presentation.animation.models import validate_animation_spec


class DemoProvider:
    provider_id = "demo"

    def names(self) -> tuple[str, ...]:
        return ("wide",)

    def get(self, name: str) -> AnimationSpec:
        if name != "wide":
            raise KeyError(name)
        return AnimationSpec(
            provider_id="demo",
            name="wide",
            frames=("◇◇", "◆◆"),
            interval_ms=120,
        )


class InvalidIntervalProvider:
    provider_id = "invalid"

    def names(self) -> tuple[str, ...]:
        return ("spinner",)

    def get(self, name: str) -> object:
        if name != "spinner":
            raise KeyError(name)
        return type(
            "RawAnimation",
            (),
            {
                "provider_id": "invalid",
                "name": "spinner",
                "frames": ("⠋",),
                "interval_ms": "fast",
            },
        )()


def test_builtin_provider_is_exact_current_spinner() -> None:
    registry = AnimationRegistry()
    spec = registry.get(BUILTIN_PROVIDER_ID, BUILTIN_ANIMATION_NAME)

    assert spec.frames == ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    assert spec.interval_ms == 80


def test_registry_accepts_structural_provider_and_lists_names() -> None:
    registry = AnimationRegistry((DemoProvider(),))

    assert registry.provider_ids() == ("demo", "openminion")
    assert registry.names("demo") == ("wide",)
    assert registry.get("demo", "wide").cell_width == 2


def test_registry_discovers_lazy_entry_point_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from openminion.cli.presentation.animation import registry as registry_module

    class DemoEntryPoint:
        name = "demo"

        def load(self) -> object:
            return DemoProvider

    monkeypatch.setattr(
        registry_module.metadata,
        "entry_points",
        lambda *, group: (DemoEntryPoint(),),
    )

    registry = AnimationRegistry()

    assert registry.provider_ids(discover=True) == ("demo", "openminion")
    assert registry.get("demo", "wide").frames == ("◇◇", "◆◆")


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (AnimationSpec("bad id", "ok", ("⠋",), 80), "invalid_provider_id"),
        (AnimationSpec("demo", "ok", (), 80), "empty_frames"),
        (AnimationSpec("demo", "ok", ("⠋\n",), 80), "control_frame"),
        (AnimationSpec("demo", "ok", ("⠋", "◇◇"), 80), "unstable_frame_width"),
        (AnimationSpec("demo", "ok", ("⠋",), 0), "invalid_interval"),
    ],
)
def test_animation_spec_validation_rejects_malformed_data(
    spec: AnimationSpec,
    reason: str,
) -> None:
    with pytest.raises(AnimationSpecError) as exc_info:
        validate_animation_spec(spec)

    assert exc_info.value.reason == reason


def test_registry_rejects_duplicate_provider_ids() -> None:
    with pytest.raises(AnimationSpecError) as exc_info:
        AnimationRegistry((BuiltInAnimationProvider(),))

    assert exc_info.value.reason == "duplicate_provider_id"


def test_registry_surfaces_malformed_provider_specs_as_typed_errors() -> None:
    with pytest.raises(AnimationSpecError) as exc_info:
        AnimationRegistry((InvalidIntervalProvider(),)).get("invalid", "spinner")

    assert exc_info.value.reason == "invalid_interval"


def test_registry_falls_back_for_unavailable_persisted_selection() -> None:
    resolution = AnimationRegistry().resolve(
        "missing",
        "spinner",
        source="preference",
        discover=False,
        allow_fallback=True,
    )

    assert resolution.spec.provider_id == "openminion"
    assert resolution.spec.name == "braille"
    assert resolution.fallback_reason == "unknown_provider"


def test_explicit_selection_error_does_not_fallback() -> None:
    with pytest.raises(AnimationSelectionError):
        resolve_focus_animation(
            Namespace(animation_provider="missing", animation="spinner"),
            registry=AnimationRegistry(),
        )


def test_parse_animation_token_supports_provider_shorthand() -> None:
    assert parse_animation_token("unicode:helix") == ("unicode", "helix")
    assert parse_animation_token("braille") == ("openminion", "braille")
