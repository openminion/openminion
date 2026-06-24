from __future__ import annotations

import os
from unittest import mock

from openminion.services.runtime.env import apply_runtime_environment


def test_non_overwrite_preserves_existing_keys_by_default():
    with mock.patch.dict(os.environ, {"ENVB04_EXISTING": "preserved"}, clear=False):
        apply_runtime_environment({"ENVB04_EXISTING": "should_not_overwrite"})
        assert os.environ["ENVB04_EXISTING"] == "preserved"


def test_explicit_overwrite_replaces_existing_key():
    with mock.patch.dict(os.environ, {"ENVB04_OVERWRITE": "old"}, clear=False):
        apply_runtime_environment({"ENVB04_OVERWRITE": "new"}, overwrite=True)
        assert os.environ["ENVB04_OVERWRITE"] == "new"


def test_new_keys_are_always_added():
    with mock.patch.dict(os.environ, {}, clear=False):
        # Use a unique name to avoid collisions
        key = "ENVB04_BRAND_NEW_KEY_ZZZ"
        os.environ.pop(key, None)
        try:
            apply_runtime_environment({key: "fresh"})
            assert os.environ.get(key) == "fresh"
        finally:
            os.environ.pop(key, None)


def test_empty_key_is_dropped_silently():
    with mock.patch.dict(os.environ, {}, clear=False):
        apply_runtime_environment({"": "value"})
        # No exception; no key inserted
        assert "" not in os.environ


def test_whitespace_only_value_is_dropped_silently():
    with mock.patch.dict(os.environ, {}, clear=False):
        key = "ENVB04_WHITESPACE_VALUE_ZZZ"
        os.environ.pop(key, None)
        try:
            apply_runtime_environment({key: "   "})
            assert key not in os.environ
        finally:
            os.environ.pop(key, None)


def test_values_are_stripped_before_write():
    with mock.patch.dict(os.environ, {}, clear=False):
        key = "ENVB04_STRIPPED_VALUE_ZZZ"
        os.environ.pop(key, None)
        try:
            apply_runtime_environment({key: "  fresh-value  "})
            assert os.environ.get(key) == "fresh-value"
        finally:
            os.environ.pop(key, None)
