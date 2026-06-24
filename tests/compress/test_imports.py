from openminion.modules.context import compress as compress_module
from openminion.modules.context.compress import (
    CompressionBudgets,
    CompressionPolicy,
    CompressionReport,
    CompressionRequest,
    CompressionResult,
    CompressedBlock,
    InputBlock,
    resolve_config_path,
)


def test_imports_available() -> None:
    assert None not in (
        CompressionBudgets,
        CompressionPolicy,
        CompressionReport,
        CompressionRequest,
        CompressionResult,
        CompressedBlock,
        InputBlock,
    )


def test_resolve_config_path(tmp_path, monkeypatch) -> None:
    fake_root = tmp_path / "context-compress"
    fake_root.mkdir()
    config_file = fake_root / "compress.yaml"
    config_file.write_text("version: 1\n", encoding="utf-8")

    monkeypatch.setattr(compress_module, "PROJECT_ROOT", fake_root)
    assert resolve_config_path() == config_file
