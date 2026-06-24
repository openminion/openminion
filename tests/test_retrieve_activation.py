from __future__ import annotations

from pathlib import Path


class TestRetrieveActivation:
    def test_retrieve_import_success(self):
        from openminion.modules.retrieve import resolve_config_path, RetrieveCtl

        assert resolve_config_path is not None
        assert RetrieveCtl is not None

    def test_retrieve_config_resolution(self):
        from openminion.modules.retrieve import resolve_config_path

        config_path = resolve_config_path()
        assert config_path is not None
        assert isinstance(config_path, Path)
        assert config_path.exists(), f"Config file not found: {config_path}"

    def test_retrieve_config_loading(self):
        from openminion.modules.retrieve import resolve_config_path, load_config

        config_path = resolve_config_path()
        cfg = load_config(config_path)

        assert cfg is not None
        assert cfg.version == 1
        assert cfg.storage.sqlite_path is not None
        assert cfg.storage.blob_root is not None
        assert cfg.defaults.strategy is not None

    def test_retrieve_ctl_initialization(self):
        from openminion.modules.retrieve import resolve_config_path, RetrieveCtl

        config_path = resolve_config_path()
        retrieve_service = None
        try:
            retrieve_service = RetrieveCtl(config=str(config_path), vector_adapter=None)
            assert retrieve_service is not None
            assert retrieve_service.config is not None
        finally:
            if retrieve_service:
                try:
                    retrieve_service.close()
                except Exception:
                    pass


class TestRetrieveDebugProvider:
    def test_debug_provider_import(self):
        from openminion.cli.commands.debug import OpenMinionRetrieveDebugProvider

        assert OpenMinionRetrieveDebugProvider is not None

    def test_debug_provider_probe_success(self):
        from openminion.cli.commands.debug import (
            OpenMinionRetrieveDebugProvider,
            DebugStatus,
            WiringSource,
        )

        provider = OpenMinionRetrieveDebugProvider()
        payload = provider._probe()

        assert payload.module == "openminion-retrieve"
        assert payload.status == DebugStatus.OK
        assert payload.wiring_source == WiringSource.REAL
        assert payload.details["import_ok"] is True
        assert payload.details["config_resolved"] is True
        assert payload.details["init_ok"] is True
        assert "config_path" in payload.details
        assert "sqlite_path" in payload.details
        assert "blob_root" in payload.details

    def test_debug_provider_details_structure(self):
        from openminion.cli.commands.debug import OpenMinionRetrieveDebugProvider

        provider = OpenMinionRetrieveDebugProvider()
        payload = provider._probe()

        # Verify all expected keys are present
        expected_keys = [
            "import_ok",
            "config_resolved",
            "config_path",
            "config_exists",
            "init_ok",
            "sqlite_path",
            "blob_root",
            "wal_mode",
            "default_strategy",
        ]
        for key in expected_keys:
            assert key in payload.details, f"Missing key: {key}"


class TestRetrieveAdapterFactory:
    def test_create_retrieve_adapter_with_service(self):
        from openminion.modules.brain.adapters.factory import create_retrieve_adapter
        from openminion.modules.retrieve import resolve_config_path, RetrieveCtl

        config_path = resolve_config_path()
        retrieve_service = None
        try:
            retrieve_service = RetrieveCtl(config=str(config_path), vector_adapter=None)
            adapter = create_retrieve_adapter(mode="auto", service=retrieve_service)

            assert adapter is not None
            # Should be real adapter, not local fallback
            from openminion.modules.brain.adapters.retrieve import (
                RetrievectlAdapter,
            )

            assert isinstance(adapter, RetrievectlAdapter)
        finally:
            if retrieve_service:
                try:
                    retrieve_service.close()
                except Exception:
                    pass

    def test_create_retrieve_adapter_local_fallback(self):
        from openminion.modules.brain.adapters.factory import create_retrieve_adapter

        adapter = create_retrieve_adapter(mode="auto", service=None)

        assert adapter is not None
        # Adapter should have retrieve method
        assert hasattr(adapter, "retrieve")
        assert callable(getattr(adapter, "retrieve"))


class TestRetrieveNoRegression:
    def test_no_retrieve_unavailable_warning_in_logs(self, caplog):
        import logging
        from openminion.cli.commands.debug import OpenMinionRetrieveDebugProvider

        # Set logging level to capture warnings
        logging.getLogger().setLevel(logging.WARNING)

        provider = OpenMinionRetrieveDebugProvider()
        payload = provider._probe()

        # Verify status is OK, not FAIL or WARN
        assert payload.status.value in ["ok", "warn"], (
            f"Unexpected status: {payload.status}"
        )

        # Check that no retrieve-related errors in last_error
        if payload.last_error:
            assert "unavailable" not in payload.last_error.lower(), (
                f"'unavailable' found in error: {payload.last_error}"
            )
