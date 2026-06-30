from typing import TYPE_CHECKING

from openminion.modules.tool.contracts.model_ids import (
    MODEL_BROWSER,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_WRITE,
    MODEL_HOST_METRICS,
    MODEL_LOCATION,
    MODEL_TIME,
    MODEL_WEATHER,
    MODEL_WEB_FETCH,
    MODEL_WEB_SEARCH,
)

from ..schemas import WorkingState
from ..tool_catalog import RunnerToolCatalog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def build_tool_inventory_response(
    runner: "BrainRunner",
    *,
    state: WorkingState,
) -> str:
    del state
    lines: list[str] = ["**Available Tools and Skills**"]
    tools_list: list[str] = sorted(RunnerToolCatalog(runner).list_tool_names())

    if not tools_list:
        tools_list = sorted(
            [
                MODEL_FILE_WRITE,
                MODEL_FILE_READ,
                MODEL_FILE_LIST_DIR,
                MODEL_FILE_FIND,
                MODEL_EXEC_RUN,
                MODEL_EXEC_POLL,
                MODEL_EXEC_KILL,
                MODEL_EXEC_LIST,
                MODEL_WEB_SEARCH,
                MODEL_WEB_FETCH,
                MODEL_WEATHER,
                MODEL_TIME,
                MODEL_LOCATION,
                MODEL_HOST_METRICS,
                MODEL_BROWSER,
            ]
        )

    lines.append(f"\n**Tools ({len(tools_list)} available):**")
    for tool in tools_list[:12]:
        lines.append(f"  • {tool}")
    if len(tools_list) > 12:
        lines.append(f"  ... and {len(tools_list) - 12} more")

    skill_count = 0
    list_skills = getattr(getattr(runner, "skill_api", None), "list_skills", None)
    try:
        if callable(list_skills):
            skills = list_skills({})
            skill_count = len(skills) if isinstance(skills, list) else 0
    except Exception:
        pass

    if skill_count > 0:
        lines.append(f"\n**Skills ({skill_count} available):**")
        lines.append("  (Use '/skill list' for detailed skill inventory)")
    else:
        lines.append("\n**Skills:** No skills currently loaded.")

    lines.append(
        "\n_To use a tool, say: `tool <name> {...}` or ask naturally (e.g., 'write to file test.txt hello')_"
    )
    return "\n".join(lines)


__all__ = ["build_tool_inventory_response"]
