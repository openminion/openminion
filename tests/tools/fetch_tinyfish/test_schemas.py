from __future__ import annotations

import pytest

from openminion.tools.fetch.providers.tinyfish.schemas import TinyFishProviderOptions


def test_tinyfish_provider_options_defaults() -> None:
    opts = TinyFishProviderOptions()
    assert opts.format == "markdown"
    assert opts.links is False
    assert opts.image_links is False


def test_tinyfish_provider_options_validation() -> None:
    opts = TinyFishProviderOptions(format="html", links=True, image_links=True)
    assert opts.format == "html"
    assert opts.links is True
    assert opts.image_links is True


def test_tinyfish_provider_options_reject_invalid_format() -> None:
    with pytest.raises(Exception):
        TinyFishProviderOptions(format="xml")
