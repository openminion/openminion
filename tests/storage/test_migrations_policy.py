from __future__ import annotations

import pytest

from openminion.modules.storage.migrations import (
    DataCompatWindow,
    DbVersionError,
    MigrationAction,
    requires_rehydrate,
    resolve_version_action,
)


def test_requires_rehydrate_when_version_outside_window():
    window = DataCompatWindow(min_data_version=3, max_data_version=5)

    assert (
        requires_rehydrate(user_version=2, window=window, has_migration_path=True)
        is True
    )


def test_resolve_version_action_prefers_rehydrate_when_supported():
    window = DataCompatWindow(min_data_version=3, max_data_version=5)

    action = resolve_version_action(
        user_version=10,
        window=window,
        has_migration_path=False,
        supports_omx=True,
    )

    assert action == MigrationAction.REHYDRATE


def test_resolve_version_action_raises_when_no_rehydrate_path():
    window = DataCompatWindow(min_data_version=3, max_data_version=5)

    with pytest.raises(DbVersionError):
        resolve_version_action(
            user_version=10,
            window=window,
            has_migration_path=False,
            supports_omx=False,
        )
