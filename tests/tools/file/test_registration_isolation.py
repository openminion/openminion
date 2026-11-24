"""Each registry must receive its own spec objects from the file plugin."""

from openminion.modules.tool import ToolRegistry
from openminion.tools.file import plugin


def test_two_registries_receive_independent_specs() -> None:
    first = ToolRegistry()
    second = ToolRegistry()
    plugin.register(first)
    plugin.register(second)

    first_read = first.list()["file.read"]
    second_read = second.list()["file.read"]

    assert first_read is not second_read
    first_read.description = "changed by the first registry"
    assert second_read.description != "changed by the first registry"


def test_registration_keeps_the_established_order_and_metadata() -> None:
    registry = ToolRegistry()
    plugin.register(registry)

    names = [name for name in registry.list() if name.startswith("file.")]
    assert names == [
        "file.list_dir",
        "file.read",
        "file.read_range",
        "file.write",
        "file.find",
        "file.trash",
        "file.search",
        "file.edit",
        "file.mkdir",
        "file.copy",
    ]
    assert registry.list()["file.mkdir"].idempotent is True
    assert registry.list()["file.copy"].block_under_readonly is True
    assert registry.list()["file.search"].idempotent is True
