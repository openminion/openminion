from unittest.mock import patch

import pytest


def test_create_skill_adapter_returns_none_in_local_mode() -> None:
    from openminion.modules.brain.adapters.factory import create_skill_adapter

    result = create_skill_adapter(mode="local", db_path=":memory:")
    assert result is None


def test_create_skill_adapter_returns_none_when_import_fails() -> None:
    with patch.dict(
        "sys.modules",
        {
            "openminion.modules.skill": None,
            "openminion.modules.skill.runtime.skill": None,
        },
    ):
        from openminion.modules.brain.adapters.factory import create_skill_adapter

        result = create_skill_adapter(mode="auto", db_path=":memory:")
        assert result is None


def test_create_skill_adapter_raises_in_strict_mode_when_import_fails() -> None:
    with patch.dict(
        "sys.modules",
        {
            "openminion.modules.skill": None,
            "openminion.modules.skill.runtime.skill": None,
        },
    ):
        from openminion.modules.brain.adapters.factory import create_skill_adapter

        with pytest.raises(ImportError):
            create_skill_adapter(mode="strict", db_path=":memory:")
