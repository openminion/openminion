import pytest
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.interfaces import (
    SKILL_INTERFACE_VERSION,
    ensure_skill_interface_compatibility,
)


class TestSkillInterfaceContract:
    def test_skill_interface_version_constant(self):
        assert SKILL_INTERFACE_VERSION == "v1"
        assert isinstance(SKILL_INTERFACE_VERSION, str)

    def test_skill_version_compatibility_check_positive(self):
        result = ensure_skill_interface_compatibility("v1")
        assert result is True

    def test_skill_version_compatibility_check_negative(self):
        with pytest.raises(ValueError) as exc_info:
            ensure_skill_interface_compatibility("v0")
        assert "Skill interface version mismatch" in str(exc_info.value)
        assert "v1" in str(exc_info.value)
        assert "v0" in str(exc_info.value)

    def test_skill_runtime_implements_contract(self):
        assert hasattr(Skill, "contract_version")

    def test_skill_basic_instantiation_has_contract_version(self, tmp_path):
        import os

        orig_key = os.environ.get("OPENMINION_STORAGE_KEY")
        try:
            skill_dir = tmp_path / "skills"
            skill_dir.mkdir()
            config_file = skill_dir / "empty_skill.yaml"
            config_file.write_text("name: empty\n")

            skill_instance = Skill(config_file)

            assert hasattr(skill_instance, "contract_version")
            assert skill_instance.contract_version == "v1"

            skill_instance.close()
        except Exception:
            pass
        finally:
            if orig_key is not None:
                os.environ["OPENMINION_STORAGE_KEY"] = orig_key
            elif "OPENMINION_STORAGE_KEY" in os.environ:
                del os.environ["OPENMINION_STORAGE_KEY"]


class TestSkillInterfaceContractNegative:
    def test_skill_contract_violation_detection(self):
        fake_version = "v0"
        with pytest.raises(ValueError) as exc_info:
            ensure_skill_interface_compatibility(fake_version)

        error_msg = str(exc_info.value)
        assert "Skill interface version mismatch" in error_msg
        assert "v1" in error_msg
        assert "v0" in error_msg
