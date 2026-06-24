from __future__ import annotations

import importlib

import pytest


def test_services_identity_does_not_export_legacy_bundle_client() -> None:
    module = importlib.import_module("openminion.services.identity")
    assert hasattr(module, "ensure_default_profile")
    assert not hasattr(module, "IdentityBundleClient")


def test_from_import_legacy_bundle_client_fails() -> None:
    with pytest.raises(ImportError):
        exec(
            "from openminion.services.identity import IdentityBundleClient",
            {},
            {},
        )
