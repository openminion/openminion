from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openminion.base.config import ConfigError, ConfigManager, OpenMinionConfig
from openminion.modules.controlplane.config import (
    from_base_config as controlplane_from_base,
)
from openminion.modules.controlplane.channels.telegram.config import (
    from_base_config as telegram_from_base,
)
from openminion.modules.identity.config import from_base_config as identity_from_base
from openminion.modules.retrieve.config import from_base_config as retrieve_from_base
from openminion.modules.skill.config import from_base_config as skill_from_base
from tests._csc_fixtures import _csc_install_default_agent


def test_from_base_config_paths_use_data_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home_root = root / "runtime"
        data_root = root / "data"
        home_root.mkdir()
        data_root.mkdir()

        base_config = OpenMinionConfig()
        _csc_install_default_agent(base_config)  # type: ignore[attr-defined]

        identity_cfg = identity_from_base(
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
        )
        assert Path(identity_cfg.storage.sqlite_path).resolve(strict=False) == (
            data_root / "identity" / "identityctl.db"
        ).resolve(strict=False)
        assert Path(identity_cfg.storage.db_path).resolve(strict=False) == (
            data_root / "identity" / "identityctl.db"
        ).resolve(strict=False)
        assert Path(identity_cfg.profiles.bundle_root).resolve(strict=False) == (
            data_root / "identity"
        ).resolve(strict=False)
        assert Path(identity_cfg.profiles.directory).resolve(strict=False) == (
            data_root / "identity" / "profiles"
        ).resolve(strict=False)

        skill_cfg = skill_from_base(
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
        )
        assert Path(skill_cfg.sqlite_path).resolve(strict=False) == (
            data_root / "skill" / "skills.db"
        ).resolve(strict=False)
        assert Path(skill_cfg.blob_root).resolve(strict=False) == (
            data_root / "skill"
        ).resolve(strict=False)

        retrieve_cfg = retrieve_from_base(
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
        )
        assert retrieve_cfg.storage.sqlite_path.resolve(strict=False) == (
            data_root / "retrieve" / "retrieve.db"
        ).resolve(strict=False)
        assert retrieve_cfg.storage.blob_root.resolve(strict=False) == (
            data_root / "retrieve" / "blobs"
        ).resolve(strict=False)

        controlplane_cfg = controlplane_from_base(
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
        )
        assert Path(controlplane_cfg.sqlite_path).resolve(strict=False) == (
            data_root / "controlplane" / "cp.db"
        ).resolve(strict=False)

        telegram_cfg = telegram_from_base(
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
        )
        polling_path = Path(telegram_cfg.telegram.polling.state_sqlite_path)
        assert polling_path.resolve(strict=False) == (
            data_root / "controlplane" / "telegram-poll-state.db"
        ).resolve(strict=False)


def test_identity_from_base_config_respects_split_identity_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home_root = root / "runtime"
        data_root = root / "data"
        home_root.mkdir()
        data_root.mkdir()

        base_config = OpenMinionConfig()
        _csc_install_default_agent(base_config)  # type: ignore[attr-defined]
        base_config.identity.db_path = "state/custom-identity.db"
        base_config.identity.bundle_root = "bundles/custom"
        base_config.identity.root = "legacy/ignored"

        identity_cfg = identity_from_base(
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
        )
        assert Path(identity_cfg.storage.sqlite_path).resolve(strict=False) == (
            home_root / "state" / "custom-identity.db"
        ).resolve(strict=False)
        assert Path(identity_cfg.storage.db_path).resolve(strict=False) == (
            home_root / "state" / "custom-identity.db"
        ).resolve(strict=False)
        assert Path(identity_cfg.profiles.bundle_root).resolve(strict=False) == (
            home_root / "bundles" / "custom"
        ).resolve(strict=False)
        assert Path(identity_cfg.profiles.directory).resolve(strict=False) == (
            home_root / "bundles" / "custom" / "profiles"
        ).resolve(strict=False)


def test_identity_from_base_config_uses_legacy_root_when_bundle_root_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home_root = root / "runtime"
        data_root = root / "data"
        home_root.mkdir()
        data_root.mkdir()

        base_config = OpenMinionConfig()
        _csc_install_default_agent(base_config)  # type: ignore[attr-defined]
        base_config.identity.root = "legacy-bundles"
        base_config.identity.bundle_root = ""

        identity_cfg = identity_from_base(
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
        )
        assert Path(identity_cfg.profiles.bundle_root).resolve(strict=False) == (
            home_root / "legacy-bundles"
        ).resolve(strict=False)
        assert Path(identity_cfg.profiles.directory).resolve(strict=False) == (
            home_root / "legacy-bundles" / "profiles"
        ).resolve(strict=False)


def test_identity_from_base_config_defaults_storage_filename_to_identityctl_db() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home_root = root / "runtime"
        data_root = root / "data"
        home_root.mkdir()
        data_root.mkdir()

        base_config = OpenMinionConfig()
        _csc_install_default_agent(base_config)  # type: ignore[attr-defined]
        base_config.identity.db_path = ""
        base_config.identity.root = ""

        identity_cfg = identity_from_base(
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
        )
        expected = (data_root / "identity" / "identityctl.db").resolve(strict=False)
        assert Path(identity_cfg.storage.sqlite_path).resolve(strict=False) == expected
        assert Path(identity_cfg.storage.db_path).resolve(strict=False) == expected


def test_config_manager_load_parses_identity_budget() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "agent.json"
        config_path.write_text(
            json.dumps(
                {
                    "context": {
                        "identity_budget": {
                            "total_tokens": 240,
                            "section_order": ["constraints", "mission", "notes"],
                            "section_priority": {"constraints": 99, "mission": 80},
                            "section_caps": {"constraints": 120, "mission": 80},
                            "truncate_strategy": "bullets",
                            "compaction": {
                                "enabled": True,
                                "provider": "openrouter",
                                "model": "minimax/minimax-m2.5",
                                "temperature": 0.1,
                                "max_tokens": 80,
                            },
                        }
                    }
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        manager = ConfigManager.load(str(config_path))
        identity_budget = manager.base_config.context.budget
        assert identity_budget is not None
        assert identity_budget.total_tokens == 240
        assert identity_budget.section_order == ["constraints", "mission", "notes"]
        assert identity_budget.section_priority["constraints"] == 99
        assert identity_budget.section_caps["constraints"] == 120
        assert identity_budget.truncate_strategy == "bullets"
        assert identity_budget.compaction.enabled is True
        assert identity_budget.compaction.max_tokens == 80
        assert (
            manager.base_config.to_dict()["context"]["identity_budget"]["total_tokens"]
            == 240
        )


def test_config_manager_load_rejects_malformed_identity_budget() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "agent.json"
        config_path.write_text(
            json.dumps(
                {
                    "context": {
                        "identity_budget": {
                            "section_order": "constraints,mission",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="section_order"):
            ConfigManager.load(str(config_path))
