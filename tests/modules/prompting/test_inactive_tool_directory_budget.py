from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.tools.shortlisting import (
    build_inactive_tool_directory_message,
)
from openminion.modules.prompting.decision import build_entry_inactive_tool_directory


def _specs(count: int, description: str = "d" * 120) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            name=f"tool.family_{index // 10}.action_{index:03d}",
            description=description,
        )
        for index in range(count)
    ]


def _directory_for(descriptions: dict[str, str]) -> str:
    specs = [
        SimpleNamespace(name=name, description=text)
        for name, text in descriptions.items()
    ]
    return build_entry_inactive_tool_directory(specs)


_READ = "Perform an operation on the selected record; read its contents."
_DELETE = "Perform an operation on the selected record; delete it permanently."


def test_large_directory_keeps_every_name_and_its_full_description() -> None:
    specs = _specs(
        104, "Read a file with offset and max_chars then return the remainder"
    )
    rendered = build_entry_inactive_tool_directory(specs)
    lines = rendered.splitlines()[1:]

    assert "(104 tools)" in rendered
    assert len(lines) == len(specs)
    for spec, line in zip(specs, lines):
        assert line == (
            f"- {spec.name}: Read a file with offset and max_chars "
            "then return the remainder"
        )


def test_swapping_read_for_delete_changes_the_rendered_directory() -> None:
    """The maintainer's probe: capabilities differing after a shared clause survive."""
    nine = {
        f"plugin.a{index}": "Perform an operation on the record."
        for index in range(3, 15)
    }
    swapped = _directory_for({"plugin.a1": _READ, "plugin.a2": _DELETE, **nine})
    source = _directory_for({"plugin.a1": _DELETE, "plugin.a2": _READ, **nine})

    assert swapped != source
    assert "- plugin.a1: " + _READ in swapped
    assert "- plugin.a2: " + _DELETE in swapped
    # Both capabilities sit beyond the shared 40-character intro and are still kept.
    assert _READ[40:] in swapped and _DELETE[40:] in source


def test_crossing_the_old_entry_limit_does_not_change_rendering() -> None:
    description = "Store a durable memory record for later retrieval in this session."
    twelve = build_entry_inactive_tool_directory(_specs(12, description))
    thirteen = build_entry_inactive_tool_directory(_specs(13, description))

    twelve_lines = twelve.splitlines()[1:]
    assert thirteen.splitlines()[1 : 1 + len(twelve_lines)] == twelve_lines
    assert f": {description}" in thirteen


def test_directory_message_excludes_active_tools_and_keeps_capabilities() -> None:
    specs = _specs(20, "Inspect a project artifact and report its metadata")
    specs.append(
        SimpleNamespace(name="web.search", description="Search the web for pages.")
    )

    message = build_inactive_tool_directory_message(
        requestable_tool_specs=specs,
        active_tool_names={"web.search"},
    )

    assert message is not None
    content = str(message.content)
    assert "web.search" not in content
    assert content.count("- tool.family_") == 20
    assert "Inspect a project artifact" in content
    assert "Search the web" not in content


def test_small_directory_keeps_descriptions() -> None:
    rendered = build_entry_inactive_tool_directory(
        [SimpleNamespace(name="memory.write", description="Store a memory.")]
    )

    assert "- memory.write: Store a memory." in rendered
    assert "(1 tools)" in rendered


def test_duplicate_names_are_rendered_once() -> None:
    specs = [
        SimpleNamespace(name="file.read", description="Read a file."),
        SimpleNamespace(name="file.read", description="Read a file, again."),
    ]

    rendered = build_entry_inactive_tool_directory(specs)

    assert rendered.count("file.read") == 1


def test_empty_names_are_dropped_and_count_reflects_them() -> None:
    specs = _specs(5) + [SimpleNamespace(name="", description="no name")]

    rendered = build_entry_inactive_tool_directory(specs)

    assert "(5 tools)" in rendered
    assert rendered.count("- tool.family_") == 5


def test_overlong_description_is_still_bounded_at_build_time() -> None:
    rendered = build_entry_inactive_tool_directory(
        [SimpleNamespace(name="exec.run", description="x" * 400)]
    )

    assert "x" * 117 in rendered
    assert "..." in rendered
    assert "x" * 118 not in rendered
