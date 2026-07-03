import pytest
from openminion.modules.secret.interfaces import (
    SECRET_INTERFACE_VERSION,
    ensure_secret_interface_compatibility,
)
from openminion.modules.secret.service import SecretService

MASTER_KEY = "TLOw6MgUwzJfjcuJ3fV_YEwVXG2oWQiv9PByOkL2-rI="


class TestSecretInterfaceContract:
    def test_secret_interface_version_constant(self):
        assert SECRET_INTERFACE_VERSION == "v1"
        assert isinstance(SECRET_INTERFACE_VERSION, str)

    def test_secret_version_compatibility_check_positive(self):
        result = ensure_secret_interface_compatibility("v1")
        assert result is True

    def test_secret_version_compatibility_check_negative(self):
        with pytest.raises(ValueError) as exc_info:
            ensure_secret_interface_compatibility("v0")
        assert "Secret interface version mismatch" in str(exc_info.value)
        assert "v1" in str(exc_info.value)
        assert "v0" in str(exc_info.value)

    def test_secret_service_implements_contract(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_SECRET_KEY", MASTER_KEY)
        service = SecretService()
        assert hasattr(service, "contract_version")
        assert service.contract_version == "v1"

    def test_secret_service_method_signatures(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_SECRET_KEY", MASTER_KEY)
        service = SecretService()
        assert hasattr(service, "set_secret")
        assert hasattr(service, "get_secret")
        assert hasattr(service, "delete_secret")
        assert hasattr(service, "list_keys")
        assert hasattr(service, "close")
        assert callable(service.set_secret)
        assert callable(service.get_secret)
        assert callable(service.delete_secret)
        assert callable(service.list_keys)
        assert callable(service.close)

    def test_secret_compatibility_with_current_implementation(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_SECRET_KEY", MASTER_KEY)
        service = SecretService()
        result = ensure_secret_interface_compatibility(service.contract_version)
        assert result is True


class TestSecretInterfaceContractNegative:
    def test_secret_contract_violation_detection(self):
        fake_version = "v0"
        with pytest.raises(ValueError) as exc_info:
            ensure_secret_interface_compatibility(fake_version)

        error_msg = str(exc_info.value)
        assert "Secret interface version mismatch" in error_msg
        assert "v1" in error_msg
        assert "v0" in error_msg
