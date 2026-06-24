from __future__ import annotations

from openminion.tools.fetch.providers.scrapling.schemas import ScraplingProviderOptions


def test_scrapling_provider_options_defaults() -> None:
    opts = ScraplingProviderOptions()
    assert opts.mode == "auto"
    assert opts.headless is True
    assert opts.max_pages == 10


def test_scrapling_provider_options_validation() -> None:
    opts = ScraplingProviderOptions(mode="dynamic", max_pages=4, solve_cloudflare=True)
    assert opts.mode == "dynamic"
    assert opts.max_pages == 4
    assert opts.solve_cloudflare is True
