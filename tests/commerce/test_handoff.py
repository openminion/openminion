from __future__ import annotations

from unittest.mock import Mock

import pytest

from openminion.tools.commerce.provider import (
    build_commerce_handoff,
    safe_commerce_links,
)


def test_handoff_keeps_only_configured_https_origin_and_path() -> None:
    handoff = build_commerce_handoff(
        reason_code="authentication_required",
        configured_base_url="https://shop.example/account/orders/one",
        candidate_url="https://shop.example/account/orders/one",
    )

    assert handoff.url == "https://shop.example/account/orders/one"
    assert handoff.requires_user_takeover is True
    assert handoff.preparation_invalidated is True
    assert handoff.requires_fresh_inspection is True
    assert handoff.requires_new_approval is True


@pytest.mark.parametrize(
    "candidate_url",
    [
        "http://shop.example/account",
        "https://other.example/account",
        "https://user:password@shop.example/account",
        "https://shop.example/other",
        "https://shop.example/account/../other",
        "https://shop.example/account/%252e%252e/other",
        "https://shop.example/account/bearer/secret",
        "https://shop.example/account/orders/one#continue",
    ],
)
def test_handoff_omits_unsafe_links(candidate_url: str) -> None:
    handoff = build_commerce_handoff(
        reason_code="unsupported_action",
        configured_base_url="https://shop.example/account",
        candidate_url=candidate_url,
    )

    assert handoff.url is None


def test_handoff_omits_signed_link_without_leaking_token() -> None:
    token = "secret-bearer-token"
    handoff = build_commerce_handoff(
        reason_code="return_label_required",
        configured_base_url="https://shop.example/returns",
        candidate_url=f"https://shop.example/returns/one?token={token}",
    )

    assert handoff.url is None
    assert token not in handoff.model_dump_json()
    assert handoff.message == (
        "Open the configured merchant surface to retrieve the return label."
    )


def test_handoff_builder_never_invokes_browser_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_mutation = Mock()
    monkeypatch.setattr(
        "openminion.tools.browser.BrowserRouter.select_provider",
        browser_mutation,
    )

    build_commerce_handoff(
        reason_code="merchant_support_required",
        configured_base_url="https://shop.example/support",
        candidate_url="https://shop.example/support",
    )

    browser_mutation.assert_not_called()


def test_commerce_links_keep_only_unsigned_configured_origin_paths() -> None:
    token = "secret-bearer-token"

    links = safe_commerce_links(
        {
            "order": "https://SHOP.example/account/orders/one",
            "signed": f"https://shop.example/account/orders/one?token={token}",
            "foreign": "https://other.example/account/orders/one",
        },
        configured_base_url="https://shop.example/account",
    )

    assert links == {"order": "https://shop.example/account/orders/one"}
    assert token not in str(links)
