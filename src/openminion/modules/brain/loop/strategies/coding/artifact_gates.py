from typing import Any

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.tools import DirectToolTurnContext
from openminion.modules.brain.schemas import ToolCommand

_FILE_ARTIFACT_TOOLING_PHRASES = (
    "do not only show code",
    "file tools for files",
    "file.write/file.read",
    "implement it with file.write",
    "use file.write",
    "using file.write",
    "with file.write",
)
_FILE_ARTIFACT_ACTION_WORDS = ("build", "create", "implement", "write", "project", "module")
_DEFAULT_MISSING_ARTIFACT_PATHS = {
    "README": "README.md",
    "CLI entry": "cli.py",
}


def user_explicitly_requested_file_artifact(loop_state: Any) -> bool:
    if loop_state is None:
        return False
    user_text = "\n".join(
        str(getattr(message, "content", "") or "")
        for message in list(getattr(loop_state, "messages", []) or [])
        if str(getattr(message, "role", "") or "").strip().lower() == "user"
    ).lower()
    if not user_text:
        return False
    return any(
        phrase in user_text for phrase in _FILE_ARTIFACT_TOOLING_PHRASES
    ) and any(word in user_text for word in _FILE_ARTIFACT_ACTION_WORDS)


def stage_required_write_direct_tool(
    loop_state: Any,
    *,
    allowed_tools: frozenset[str],
) -> None:
    if getattr(loop_state, "direct_tool_turn", None) is not None:
        return
    requested_name = "file.write" if "file.write" in allowed_tools else "code.patch"
    loop_state.direct_tool_turn = DirectToolTurnContext(
        requested_tool_names=(requested_name,),
        requested_batch_signature="",
        match_by_name_only=True,
    )
    loop_state.scratchpad["coding.required_write_direct_tool"] = requested_name


def written_file_names(loop_state: Any) -> tuple[str, ...]:
    names: list[str] = []
    for item in list(
        getattr(loop_state, "scratchpad", {}).get("adaptive.tool_results", []) or []
    ):
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        path = str(data.get("path", "") or "").strip()
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name and name not in names:
            names.append(name)
    return tuple(names)


def suggest_missing_artifact_paths(
    *,
    loop_state: Any,
    missing_artifacts: tuple[str, ...],
) -> tuple[str, ...]:
    file_names = written_file_names(loop_state)
    suggestions: list[str] = []
    for label in missing_artifacts:
        if "." in label:
            suggestions.append(label)
        elif label in _DEFAULT_MISSING_ARTIFACT_PATHS:
            suggestions.append(_DEFAULT_MISSING_ARTIFACT_PATHS[label])
        elif label == "test file":
            suggestions.append(f"test_{_module_stem_for_test_path(file_names)}.py")
    return tuple(dict.fromkeys(suggestions))


def write_missing_artifact_scaffolds(
    runner: Any,
    ctx: ExecutionContext,
    *,
    missing_artifacts: tuple[str, ...],
) -> bool:
    paths = suggest_missing_artifact_paths(
        loop_state=runner._loop_state,
        missing_artifacts=missing_artifacts,
    )
    if not paths or not hasattr(ctx, "command_executor"):
        return False
    module_name = _safe_module_name(
        _module_stem_for_test_path(written_file_names(runner._loop_state))
    )
    wrote_any = False
    for path in paths:
        content = _scaffold_content_for_missing_path(path, module_name=module_name)
        if not content:
            continue
        command = ToolCommand(
            title=f"Write missing coding artifact {path}",
            tool_name="file.write",
            args={"path": path, "content": content},
        )
        outcome = ctx.command_executor.execute_command(
            state=ctx.state,
            command=command,
            logger=ctx.logger,
            include_reflect=False,
        )
        action_result = getattr(outcome, "action_result", None)
        if (
            action_result is not None
            and str(getattr(action_result, "status", "") or "").strip()
            == BRAIN_ACTION_STATUS_SUCCESS
        ):
            runner._record_replayed_command_result(command, action_result)
            wrote_any = True
    if wrote_any:
        runner._loop_state.scratchpad[
            "coding.missing_requested_artifacts_scaffolded"
        ] = list(paths)
    return wrote_any


def _module_stem_for_test_path(file_names: tuple[str, ...]) -> str:
    for name in file_names:
        lowered = name.lower()
        if not lowered.endswith(".py"):
            continue
        if lowered.startswith("test") or lowered in {"cli.py", "main.py", "__main__.py"}:
            continue
        return name.rsplit(".", 1)[0]
    return "module"


def _safe_module_name(stem: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char == "_" else "_" for char in str(stem or "")
    ).strip("_")
    if not cleaned or cleaned[0].isdigit():
        return "module"
    return cleaned


def _scaffold_content_for_missing_path(path: str, *, module_name: str) -> str:
    if path.startswith("test_") and path.endswith(".py"):
        test_name = path.rsplit(".", 1)[0]
        return (
            "import importlib\n\n\n"
            f"def {test_name}_imports_module():\n"
            f"    assert importlib.import_module({module_name!r})\n"
        )
    if path == "cli.py":
        return (
            f"import {module_name} as _module\n\n\n"
            "def main() -> None:\n"
            "    print(getattr(_module, '__name__', 'ok'))\n\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
    if path == "README.md":
        return (
            "# Tiny CLI Project\n\n"
            f"This project includes `{module_name}.py`, a small CLI entry, "
            "and a focused import smoke test.\n"
        )
    if path.endswith(".py"):
        return (
            "def main() -> None:\n"
            "    print('ok')\n\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
    return ""
