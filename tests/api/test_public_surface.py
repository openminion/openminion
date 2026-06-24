from __future__ import annotations

import importlib


# Symbols re-exported at ``import openminion``. ISAP-02 ships the minimum
# surface (APIRuntime + OpenMinionConfig); the rest grow in as later tasks
# close.
EXPECTED_OPENMINION_PUBLIC = {
    "APIRuntime",
    "Agent",
    "AgentOutputValidationError",
    "AgentRunResult",
    "Handoff",
    "MemoryBundle",
    "OpenMinionConfig",
    "__version__",
    "subagent",  # ISAP-15
    "tool",  # ISAP-11
}

# Symbols re-exported at ``import openminion.api``. ``dispatch_request`` is
# pre-existing; ``APIRuntime`` is added by ISAP-02; the ``Agent`` triplet is
# added by ISAP-10; ``Handoff`` + ``subagent`` are added by ISAP-14/15.
EXPECTED_OPENMINION_API_PUBLIC = {
    "APIRuntime",
    "Agent",
    "AgentOutputValidationError",
    "AgentRunResult",
    "Handoff",
    "dispatch_request",
    "subagent",
}


def _public_names(module_name: str) -> set[str]:
    module = importlib.import_module(module_name)
    return set(getattr(module, "__all__", ()))


def test_openminion_public_surface_pinned() -> None:
    actual = _public_names("openminion")
    assert actual == EXPECTED_OPENMINION_PUBLIC, (
        "openminion public surface drift. "
        f"Added: {actual - EXPECTED_OPENMINION_PUBLIC}, "
        f"Removed: {EXPECTED_OPENMINION_PUBLIC - actual}. "
        "Update EXPECTED_OPENMINION_PUBLIC in this test together with "
        "the ISAP task that adds the symbol."
    )


def test_openminion_api_public_surface_pinned() -> None:
    actual = _public_names("openminion.api")
    assert actual == EXPECTED_OPENMINION_API_PUBLIC, (
        "openminion.api public surface drift. "
        f"Added: {actual - EXPECTED_OPENMINION_API_PUBLIC}, "
        f"Removed: {EXPECTED_OPENMINION_API_PUBLIC - actual}. "
        "Update EXPECTED_OPENMINION_API_PUBLIC in this test together with "
        "the ISAP task that adds the symbol."
    )


def test_openminion_apiruntime_importable_from_root() -> None:

    import openminion

    assert openminion.APIRuntime is not None
    assert openminion.OpenMinionConfig is not None


def test_openminion_since_metadata_covers_public_surface() -> None:

    import openminion

    missing = EXPECTED_OPENMINION_PUBLIC - set(openminion.__since__.keys())
    assert not missing, (
        "openminion.__since__ is missing version entries for: "
        f"{sorted(missing)}. Add an entry pointing at the version that "
        "first exposed the symbol on the stable public surface."
    )


def test_openminion_since_versions_look_like_semver() -> None:

    import openminion

    for symbol, version in openminion.__since__.items():
        assert isinstance(version, str) and version, (
            f"openminion.__since__[{symbol!r}] is empty or non-string."
        )
        parts = version.split(".")
        assert len(parts) >= 2 and parts[0].isdigit(), (
            f"openminion.__since__[{symbol!r}]={version!r} is not semver-ish."
        )
