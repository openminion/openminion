from .model_ids import (
    MODEL_BROWSER,
    MODEL_CODE_GREP,
    MODEL_CODE_PATCH,
    MODEL_CODE_REPO_INDEX,
    MODEL_CODE_REPO_MAP,
    MODEL_CODE_SYMBOL_FIND,
    MODEL_EXEC_CLEAR,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_PASTE,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_EXEC_SEND_KEYS,
    MODEL_EXEC_SUBMIT,
    MODEL_FILE_EDIT,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_READ_RANGE,
    MODEL_FILE_SEARCH,
    MODEL_FILE_TRASH,
    MODEL_FILE_WRITE,
    MODEL_GITHUB_FETCH_CHECKS,
    MODEL_GITHUB_FETCH_COMMENTS,
    MODEL_GITHUB_FETCH_DIFF,
    MODEL_GITHUB_FETCH_PR,
    MODEL_AGENT_GET,
    MODEL_AGENT_LIST,
    MODEL_TASK_DELEGATE,
    MODEL_GITHUB_LIST_PRS,
    MODEL_GITHUB_COMMIT_FILES,
    MODEL_GITHUB_OPEN_PR,
    MODEL_GITHUB_POST_PR_COMMENT,
    MODEL_GITHUB_POST_PR_REVIEW,
    MODEL_GIT_ADD,
    MODEL_GIT_BLAME,
    MODEL_GIT_BRANCH,
    MODEL_GIT_CHECKOUT,
    MODEL_GIT_COMMIT,
    MODEL_GIT_DIFF,
    MODEL_GIT_LOG,
    MODEL_GIT_REFLOG,
    MODEL_GIT_RESET,
    MODEL_GIT_SHOW,
    MODEL_GIT_STASH,
    MODEL_GIT_STATUS,
    MODEL_GWS_AUTH_EXPORT,
    MODEL_GWS_AUTH_LOGIN,
    MODEL_GWS_AUTH_SETUP,
    MODEL_GWS_CALL,
    MODEL_GWS_SCHEMA,
    MODEL_IP_LOCAL,
    MODEL_IP_PUBLIC,
    MODEL_LOCATION,
    MODEL_MEMORY_FORGET,
    MODEL_MEMORY_SEARCH,
    MODEL_MEMORY_WRITE,
    MODEL_SKILL_GET,
    MODEL_SKILL_INGEST,
    MODEL_SKILL_INGEST_URL,
    MODEL_SKILL_INSPECT,
    MODEL_SKILL_LIST,
    MODEL_SKILL_REMOVE,
    MODEL_TASK_CANCEL,
    MODEL_TASK_CONSOLIDATE_MEMORY,
    MODEL_TASK_LIST,
    MODEL_TASK_PAUSE,
    MODEL_TASK_RESUME,
    MODEL_TASK_SCHEDULE,
    MODEL_TASK_SHOW,
    MODEL_TASK_WATCH,
    MODEL_PLAN_ADD,
    MODEL_PLAN_CLEAR,
    MODEL_PLAN_COMPLETE,
    MODEL_PLAN_LIST,
    MODEL_PLAN_SET,
    MODEL_PLAN_UPDATE,
    MODEL_TODO_WRITE,
    MODEL_TIME,
    MODEL_TOOL_AUTHOR,
    MODEL_TOOL_GET,
    MODEL_TOOL_INSPECT,
    MODEL_TOOL_LIST,
    MODEL_TOOL_REGISTER,
    MODEL_WEATHER,
    MODEL_WEB_FETCH,
    MODEL_WEB_SEARCH,
)
from .normalization import normalize_raw_model_tool_name


_RUNTIME_PREFIX = "runtime."


MODEL_TOOL_DISPLAY_NAME_MAP: dict[str, str] = {
    MODEL_FILE_LIST_DIR: "List Directory",
    MODEL_FILE_READ: "Read File",
    MODEL_FILE_READ_RANGE: "Read File Range",
    MODEL_FILE_WRITE: "Write File",
    MODEL_FILE_FIND: "Find File",
    MODEL_FILE_TRASH: "Trash File",
    MODEL_FILE_SEARCH: "Search Files",
    MODEL_FILE_EDIT: "Edit File",
    MODEL_CODE_PATCH: "Apply Patch",
    MODEL_CODE_GREP: "Search Code",
    MODEL_CODE_REPO_MAP: "Map Repository",
    MODEL_CODE_SYMBOL_FIND: "Find Symbol",
    MODEL_TOOL_LIST: "List Tools",
    MODEL_EXEC_RUN: "Run Command",
    MODEL_EXEC_POLL: "Check Command",
    MODEL_EXEC_KILL: "Stop Command",
    MODEL_EXEC_LIST: "List Commands",
    MODEL_EXEC_CLEAR: "Clear Command",
    MODEL_EXEC_PASTE: "Paste Into Command",
    MODEL_EXEC_SEND_KEYS: "Send Keys",
    MODEL_EXEC_SUBMIT: "Submit Command",
    MODEL_WEB_SEARCH: "Web Search",
    MODEL_WEB_FETCH: "Web Fetch",
    MODEL_WEATHER: "Weather",
    MODEL_TIME: "Time",
    MODEL_LOCATION: "Location",
    MODEL_IP_PUBLIC: "Public IP",
    MODEL_IP_LOCAL: "Local IP",
    MODEL_BROWSER: "Browser",
    MODEL_GWS_CALL: "Google Workspace",
    MODEL_GWS_SCHEMA: "Google Workspace Schema",
    MODEL_GWS_AUTH_SETUP: "Google Workspace Setup",
    MODEL_GWS_AUTH_LOGIN: "Google Workspace Login",
    MODEL_GWS_AUTH_EXPORT: "Google Workspace Export",
    MODEL_SKILL_INGEST: "Ingest Skill",
    MODEL_SKILL_INGEST_URL: "Ingest Skill From URL",
    MODEL_SKILL_INSPECT: "Inspect Skill",
    MODEL_SKILL_LIST: "List Skills",
    MODEL_SKILL_GET: "Get Skill",
    MODEL_SKILL_REMOVE: "Remove Skill",
    MODEL_MEMORY_WRITE: "Write Memory",
    MODEL_MEMORY_SEARCH: "Search Memory",
    MODEL_MEMORY_FORGET: "Forget Memory",
    MODEL_TASK_SCHEDULE: "Schedule Task",
    MODEL_TASK_CONSOLIDATE_MEMORY: "Consolidate Memory",
    MODEL_TASK_WATCH: "Watch Task",
    MODEL_TASK_CANCEL: "Cancel Task",
    MODEL_TASK_LIST: "List Tasks",
    MODEL_TASK_PAUSE: "Pause Task",
    MODEL_TASK_RESUME: "Resume Task",
    MODEL_TASK_SHOW: "Show Task",
    MODEL_GITHUB_LIST_PRS: "List GitHub PRs",
    MODEL_GITHUB_FETCH_PR: "Fetch GitHub PR",
    MODEL_GITHUB_FETCH_DIFF: "Fetch GitHub Diff",
    MODEL_GITHUB_FETCH_COMMENTS: "Fetch GitHub Comments",
    MODEL_GITHUB_FETCH_CHECKS: "Fetch GitHub Checks",
    MODEL_GITHUB_COMMIT_FILES: "Commit GitHub Files",
    MODEL_GITHUB_OPEN_PR: "Open GitHub PR",
    MODEL_GITHUB_POST_PR_REVIEW: "Post GitHub PR Review",
    MODEL_GITHUB_POST_PR_COMMENT: "Post GitHub PR Comment",
    MODEL_GIT_STATUS: "Git Status",
    MODEL_GIT_DIFF: "Git Diff",
    MODEL_GIT_LOG: "Git Log",
    MODEL_GIT_SHOW: "Git Show",
    MODEL_GIT_BLAME: "Git Blame",
    MODEL_GIT_BRANCH: "Git Branch",
    MODEL_GIT_CHECKOUT: "Git Checkout",
    MODEL_GIT_ADD: "Git Add",
    MODEL_GIT_COMMIT: "Git Commit",
    MODEL_GIT_STASH: "Git Stash",
    MODEL_GIT_RESET: "Git Reset",
    MODEL_GIT_REFLOG: "Git Reflog",
    MODEL_PLAN_SET: "Set Plan",
    MODEL_PLAN_ADD: "Add Plan Step",
    MODEL_PLAN_UPDATE: "Update Plan Step",
    MODEL_PLAN_COMPLETE: "Complete Plan Step",
    MODEL_PLAN_LIST: "List Plan",
    MODEL_PLAN_CLEAR: "Clear Plan",
    MODEL_TODO_WRITE: "Write Todos",
    MODEL_TOOL_GET: "Get Tool",
    MODEL_TOOL_AUTHOR: "Author Tool",
    MODEL_TOOL_INSPECT: "Inspect Tool",
    MODEL_TOOL_REGISTER: "Register Tool",
    MODEL_CODE_REPO_INDEX: "Index Repository",
    MODEL_TASK_DELEGATE: "Delegate Task",
    MODEL_AGENT_LIST: "List Agents",
    MODEL_AGENT_GET: "Get Agent",
}


def _display_name_for_token(token: str) -> str | None:
    direct = MODEL_TOOL_DISPLAY_NAME_MAP.get(token)
    if direct is not None:
        return direct
    canonical = normalize_raw_model_tool_name(token)
    if canonical is None:
        return None
    return MODEL_TOOL_DISPLAY_NAME_MAP.get(canonical)


def display_name_for_tool_name(tool_name: str) -> str:
    """Resolve a friendly user-facing label for a tool token."""
    token = str(tool_name or "").strip()
    if not token:
        return token

    resolved = _display_name_for_token(token)
    if resolved is not None:
        return resolved

    if token.startswith(_RUNTIME_PREFIX):
        stripped = token[len(_RUNTIME_PREFIX) :]
        if stripped:
            stripped_resolved = _display_name_for_token(stripped)
            if stripped_resolved is not None:
                return stripped_resolved

    return token


__all__ = [
    "MODEL_TOOL_DISPLAY_NAME_MAP",
    "display_name_for_tool_name",
]
