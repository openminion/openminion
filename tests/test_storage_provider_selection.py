from __future__ import annotations

from unittest.mock import patch

import pytest

from openminion.modules.retrieve.config import (
    ConfigError as RetrieveConfigError,
    load_config as load_retrieve_config,
)
from openminion.modules.session.storage.store import SQLiteSessionStore
from openminion.modules.skill.config import load_config as load_skill_config
from openminion.modules.storage.runtime.provider_selection import (
    resolve_storage_provider,
)


def test_retrieve_storage_provider_defaults_to_sqlite() -> None:
    cfg = load_retrieve_config({"retrievectl": {"storage": {}}})
    assert cfg.storage.provider == "sqlite"


def test_retrieve_storage_provider_emits_selected_event(caplog) -> None:
    with caplog.at_level("INFO", logger="openminion.storage"):
        cfg = load_retrieve_config({"retrievectl": {"storage": {}}})
    assert cfg.storage.provider == "sqlite"
    assert any(
        "storage_provider_selected module=retrieve provider=sqlite" in record.message
        for record in caplog.records
    )


def test_retrieve_storage_provider_rejects_unknown(caplog) -> None:
    with caplog.at_level("WARNING", logger="openminion.storage"):
        with pytest.raises(RetrieveConfigError) as ctx:
            load_retrieve_config(
                {
                    "retrievectl": {
                        "storage": {
                            "provider": "remote",
                        }
                    }
                }
            )
    assert any(
        "storage_provider_rejected module=retrieve provider=remote" in record.message
        for record in caplog.records
    )
    assert "Unsupported retrievectl.storage.provider" in str(ctx.value)


def test_skill_storage_provider_emits_selected_event(caplog) -> None:
    with caplog.at_level("INFO", logger="openminion.storage"):
        cfg = load_skill_config({"skill": {}})
    assert cfg.provider == "sqlite"
    assert any(
        "storage_provider_selected module=skill provider=sqlite" in record.message
        for record in caplog.records
    )


def test_skill_storage_provider_rejects_unknown(caplog) -> None:
    with caplog.at_level("WARNING", logger="openminion.storage"):
        with pytest.raises(ValueError) as ctx:
            load_skill_config({"skill": {"provider": "remote"}})
    assert any(
        "storage_provider_rejected module=skill provider=remote" in record.message
        for record in caplog.records
    )
    assert "Unsupported skill storage provider" in str(ctx.value)


def test_session_storage_provider_rejects_unknown_env(caplog) -> None:
    with caplog.at_level("WARNING", logger="openminion.storage"):
        with patch.dict(
            "os.environ",
            {"OPENMINION_SESSION_STORAGE_PROVIDER": "remote"},
            clear=False,
        ):
            with pytest.raises(RuntimeError) as ctx:
                SQLiteSessionStore(":memory:")
    assert any(
        "storage_provider_rejected module=session provider=remote" in record.message
        for record in caplog.records
    )
    assert "Unsupported session storage provider" in str(ctx.value)


def test_session_storage_provider_emits_selected_event(caplog) -> None:
    with caplog.at_level("INFO", logger="openminion.storage"):
        with patch.dict(
            "os.environ",
            {"OPENMINION_SESSION_STORAGE_PROVIDER": "sqlite"},
            clear=False,
        ):
            store = SQLiteSessionStore(":memory:")
            store.close()
    assert any(
        "storage_provider_selected module=session provider=sqlite" in record.message
        for record in caplog.records
    )


def test_skill_storage_provider_defaults_to_sqlite() -> None:
    cfg = load_skill_config({"skill": {}})
    assert cfg.provider == "sqlite"


def test_resolve_storage_provider_normalizes_and_logs_selected(caplog) -> None:
    with caplog.at_level("INFO", logger="openminion.storage"):
        provider = resolve_storage_provider(
            module="testmod",
            raw_provider=" SQLITE ",
            source_label="test.provider",
            path_mode="integrated_runtime",
        )
    assert provider == "sqlite"
    assert any(
        "storage_provider_selected module=testmod provider=sqlite path_mode=integrated_runtime"
        in record.message
        for record in caplog.records
    )


def test_resolve_storage_provider_rejects_unknown(caplog) -> None:
    with caplog.at_level("WARNING", logger="openminion.storage"):
        with pytest.raises(ValueError) as ctx:
            resolve_storage_provider(
                module="testmod",
                raw_provider="remote",
                source_label="test.provider",
            )
    assert "Unsupported test.provider='remote'. Supported provider: sqlite." in str(
        ctx.value
    )
    assert any(
        "storage_provider_rejected module=testmod provider=remote reason=unsupported"
        in record.message
        for record in caplog.records
    )
