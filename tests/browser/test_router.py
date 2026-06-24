from __future__ import annotations

from dataclasses import dataclass, field

from openminion.tools.browser import (
    BrowserCapabilities,
    BrowserProviderRegistry,
    BrowserRouter,
    BrowserRoutingConfig,
)


@dataclass
class _Provider:
    provider_id: str
    capabilities: BrowserCapabilities = field(default_factory=BrowserCapabilities)
    provider_version: str = "test"


def test_router_precedence() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider("arg"))
    reg.register(_Provider("profile"))
    reg.register(_Provider("session"))
    reg.register(_Provider("default"))

    router = BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="default"))

    assert (
        router.select_provider(
            requested_provider="arg",
            agent_profile_provider="profile",
            session_provider_override="session",
        ).provider_id
        == "arg"
    )
    assert (
        router.select_provider(
            requested_provider=None,
            agent_profile_provider="profile",
            session_provider_override="session",
        ).provider_id
        == "profile"
    )
    assert (
        router.select_provider(
            requested_provider="",
            agent_profile_provider="",
            session_provider_override="session",
        ).provider_id
        == "session"
    )
    assert (
        router.select_provider(
            requested_provider="",
            agent_profile_provider="",
            session_provider_override="",
        ).provider_id
        == "default"
    )


def test_router_affinity_routes_tab_without_explicit_provider() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider("pinchtab"))
    reg.register(_Provider("playwright"))
    router = BrowserRouter(
        reg, config=BrowserRoutingConfig(default_provider="pinchtab")
    )

    router.remember_affinity(provider_id="playwright", tab_id="tab_123")
    resolved = router.select_provider(
        requested_provider="",
        agent_profile_provider="",
        session_provider_override="",
        tab_id="tab_123",
    )
    assert resolved.provider_id == "playwright"


def test_router_auto_selects_pinchtab_when_no_default_configured() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider("playwright"))
    reg.register(_Provider("pinchtab"))
    router = BrowserRouter(reg, config=BrowserRoutingConfig(default_provider=""))

    resolved = router.select_provider(
        requested_provider="",
        agent_profile_provider="",
        session_provider_override="",
    )
    assert resolved.provider_id == "pinchtab"


def test_router_auto_selects_first_provider_when_no_preferred_available() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider("zeta"))
    reg.register(_Provider("alpha"))
    router = BrowserRouter(reg, config=BrowserRoutingConfig(default_provider=""))

    resolved = router.select_provider(
        requested_provider="",
        agent_profile_provider="",
        session_provider_override="",
    )
    assert resolved.provider_id == "alpha"


def test_router_runtime_default_provider_overrides_implicit_default() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider("playwright"))
    reg.register(_Provider("pinchtab"))
    router = BrowserRouter(reg, config=BrowserRoutingConfig(default_provider=""))

    resolved = router.select_provider(
        requested_provider="",
        agent_profile_provider="",
        session_provider_override="",
        runtime_default_provider="playwright",
    )

    assert resolved.provider_id == "playwright"


def test_router_treats_requested_auto_as_implicit_default() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider("playwright"))
    reg.register(_Provider("pinchtab"))
    router = BrowserRouter(reg, config=BrowserRoutingConfig(default_provider=""))

    resolved = router.select_provider(
        requested_provider="auto",
        agent_profile_provider="",
        session_provider_override="",
        runtime_default_provider="playwright",
    )

    assert resolved.provider_id == "playwright"


def test_router_runtime_provider_order_overrides_legacy_auto_default() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider("playwright"))
    reg.register(_Provider("pinchtab"))
    router = BrowserRouter(reg, config=BrowserRoutingConfig(default_provider=""))

    resolved = router.select_provider(
        requested_provider="",
        agent_profile_provider="",
        session_provider_override="",
        runtime_provider_order=("playwright", "pinchtab"),
    )

    assert resolved.provider_id == "playwright"


def test_router_affinity_precedes_runtime_preferences() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider("pinchtab"))
    reg.register(_Provider("playwright"))
    router = BrowserRouter(
        reg,
        config=BrowserRoutingConfig(default_provider="pinchtab"),
    )
    router.remember_affinity(provider_id="playwright", tab_id="tab-1")

    resolved = router.select_provider(
        requested_provider="",
        agent_profile_provider="",
        session_provider_override="",
        tab_id="tab-1",
        runtime_default_provider="pinchtab",
        runtime_provider_order=("pinchtab",),
    )

    assert resolved.provider_id == "playwright"


def test_router_full_precedence_chain_pinchtab_playwright_coexistence() -> None:
    reg = BrowserProviderRegistry()
    for pid in ("alpha", "beta", "gamma", "delta", "pinchtab", "playwright"):
        reg.register(_Provider(pid))

    router = BrowserRouter(
        reg,
        config=BrowserRoutingConfig(
            default_provider="delta",
            provider_order=("pinchtab", "playwright"),
        ),
    )
    router.remember_affinity(provider_id="gamma", tab_id="tab-chain")
    router.remember_affinity(provider_id="beta", instance_id="inst-chain")

    # 1. requested wins over everything.
    assert (
        router.select_provider(
            requested_provider="alpha",
            agent_profile_provider="beta",
            session_provider_override="gamma",
            tab_id="tab-chain",
            instance_id="inst-chain",
            runtime_default_provider="delta",
            runtime_provider_order=("pinchtab",),
        ).provider_id
        == "alpha"
    )

    # 2. profile wins over session/affinity/runtime/config when requested is empty.
    assert (
        router.select_provider(
            requested_provider="",
            agent_profile_provider="beta",
            session_provider_override="gamma",
            tab_id="tab-chain",
            instance_id="inst-chain",
            runtime_default_provider="delta",
            runtime_provider_order=("pinchtab",),
        ).provider_id
        == "beta"
    )

    # 3. session wins over affinity/runtime/config when requested+profile are empty.
    assert (
        router.select_provider(
            requested_provider="",
            agent_profile_provider="",
            session_provider_override="gamma",
            tab_id="tab-chain",
            instance_id="inst-chain",
            runtime_default_provider="delta",
            runtime_provider_order=("pinchtab",),
        ).provider_id
        == "gamma"
    )

    # 4. tab affinity wins over instance affinity, runtime, config when above empty.
    assert (
        router.select_provider(
            requested_provider="",
            agent_profile_provider="",
            session_provider_override="",
            tab_id="tab-chain",
            instance_id="inst-chain",
            runtime_default_provider="delta",
            runtime_provider_order=("pinchtab",),
        ).provider_id
        == "gamma"
    )

    # 5. instance affinity wins over runtime/config when tab affinity is missing.
    assert (
        router.select_provider(
            requested_provider="",
            agent_profile_provider="",
            session_provider_override="",
            tab_id=None,
            instance_id="inst-chain",
            runtime_default_provider="delta",
            runtime_provider_order=("pinchtab",),
        ).provider_id
        == "beta"
    )

    # 6. runtime default wins over config default when no affinity.
    assert (
        router.select_provider(
            requested_provider="",
            agent_profile_provider="",
            session_provider_override="",
            tab_id=None,
            instance_id=None,
            runtime_default_provider="alpha",
            runtime_provider_order=(),
        ).provider_id
        == "alpha"
    )

    # 7. config default wins when no runtime override.
    assert (
        router.select_provider(
            requested_provider="",
            agent_profile_provider="",
            session_provider_override="",
            tab_id=None,
            instance_id=None,
            runtime_default_provider=None,
            runtime_provider_order=(),
        ).provider_id
        == "delta"
    )


def test_router_negative_no_providers_registered_raises_keyerror() -> None:
    reg = BrowserProviderRegistry()
    router = BrowserRouter(reg, config=BrowserRoutingConfig(default_provider=""))

    try:
        router.select_provider(
            requested_provider="",
            agent_profile_provider="",
            session_provider_override="",
        )
    except KeyError as exc:
        assert "no browser provider" in str(exc)
    else:  # pragma: no cover - regression guard
        raise AssertionError("expected KeyError when registry is empty")
