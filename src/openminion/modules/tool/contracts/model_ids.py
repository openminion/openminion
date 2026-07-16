# Canonical model-facing tool IDs.
MODEL_FILE_LIST_DIR = "file.list_dir"
MODEL_FILE_READ = "file.read"
MODEL_FILE_READ_RANGE = "file.read_range"
MODEL_FILE_WRITE = "file.write"
MODEL_FILE_FIND = "file.find"
MODEL_FILE_TRASH = "file.trash"
MODEL_FILE_SEARCH = "file.search"
MODEL_FILE_EDIT = "file.edit"

MODEL_CODE_PATCH = "code.patch"
MODEL_CODE_GREP = "code.grep"
MODEL_CODE_REPO_MAP = "code.repo_map"
MODEL_CODE_REPO_INDEX = "code.repo_index"
MODEL_CODE_SYMBOL_FIND = "code.symbol_find"

MODEL_TOOL_LIST = "tool.list"
MODEL_TOOL_SEARCH = (
    "tool.search"  # compatibility alias for tool.list; prefer MODEL_TOOL_LIST
)
MODEL_TOOL_GET = "tool.get"
MODEL_TOOL_AUTHOR = "tool.author"
MODEL_TOOL_INSPECT = "tool.inspect"
MODEL_TOOL_REGISTER = "tool.register"

MODEL_EXEC_RUN = "exec.run"
MODEL_EXEC_POLL = "exec.poll"
MODEL_EXEC_KILL = "exec.kill"
MODEL_EXEC_LIST = "exec.list"
MODEL_EXEC_CLEAR = "exec.clear"
MODEL_EXEC_PASTE = "exec.paste"
MODEL_EXEC_SEND_KEYS = "exec.send_keys"
MODEL_EXEC_SUBMIT = "exec.submit"

MODEL_WEB_SEARCH = "web.search"
MODEL_WEB_FETCH = "web.fetch"
MODEL_WEATHER = "weather"
MODEL_TIME = "time"
MODEL_LOCATION = "location"
MODEL_HOST_METRICS = "host.metrics"
MODEL_IP_PUBLIC = "ip.public"
MODEL_IP_LOCAL = "ip.local"
MODEL_BROWSER = "browser"

MODEL_OPS_TARGET_LIST = "ops.target.list"
MODEL_OPS_TARGET_INSPECT = "ops.target.inspect"
MODEL_OPS_HOST_SNAPSHOT = "ops.host.snapshot"
MODEL_OPS_SERVICE_INSPECT = "ops.service.inspect"
MODEL_OPS_LOGS_QUERY = "ops.logs.query"
MODEL_OPS_NETWORK_INSPECT = "ops.network.inspect"
MODEL_OPS_COMMAND_OBSERVE = "ops.command.observe"
MODEL_OPS_JOB_INSPECT = "ops.job.inspect"
MODEL_OPS_JOB_CANCEL = "ops.job.cancel"

OPS_MODEL_TOOL_IDS: tuple[str, ...] = (
    MODEL_OPS_TARGET_LIST,
    MODEL_OPS_TARGET_INSPECT,
    MODEL_OPS_HOST_SNAPSHOT,
    MODEL_OPS_SERVICE_INSPECT,
    MODEL_OPS_LOGS_QUERY,
    MODEL_OPS_NETWORK_INSPECT,
    MODEL_OPS_COMMAND_OBSERVE,
    MODEL_OPS_JOB_INSPECT,
    MODEL_OPS_JOB_CANCEL,
)

MODEL_GWS_CALL = "gws.call"
MODEL_GWS_SCHEMA = "gws.schema"
MODEL_GWS_AUTH_SETUP = "gws.auth.setup"
MODEL_GWS_AUTH_LOGIN = "gws.auth.login"
MODEL_GWS_AUTH_EXPORT = "gws.auth.export"

MODEL_SKILL_INGEST = "skill.ingest"
MODEL_SKILL_INGEST_URL = "skill.ingest_url"
MODEL_SKILL_INSPECT = "skill.inspect"
MODEL_SKILL_LIST = "skill.list"
MODEL_SKILL_GET = "skill.get"
MODEL_SKILL_REMOVE = "skill.remove"
MODEL_MEMORY_WRITE = "memory.write"
MODEL_MEMORY_SEARCH = "memory.search"
MODEL_MEMORY_FORGET = "memory.forget"
MODEL_GIT_STATUS = "git.status"
MODEL_GIT_DIFF = "git.diff"
MODEL_GIT_LOG = "git.log"
MODEL_GIT_SHOW = "git.show"
MODEL_GIT_BLAME = "git.blame"
MODEL_GIT_BRANCH = "git.branch"
MODEL_GIT_CHECKOUT = "git.checkout"
MODEL_GIT_ADD = "git.add"
MODEL_GIT_COMMIT = "git.commit"
MODEL_GIT_STASH = "git.stash"
MODEL_GIT_RESET = "git.reset"
MODEL_GIT_REFLOG = "git.reflog"

MODEL_PLAN_SET = "plan.set"
MODEL_PLAN_ADD = "plan.add"
MODEL_PLAN_UPDATE = "plan.update"
MODEL_PLAN_COMPLETE = "plan.complete"
MODEL_PLAN_LIST = "plan.list"
MODEL_PLAN_CLEAR = "plan.clear"
MODEL_TODO_WRITE = "todo.write"
MODEL_TASK_SCHEDULE = "task.schedule"
MODEL_TASK_CONSOLIDATE_MEMORY = "task.consolidate_memory"
MODEL_TASK_WATCH = "task.watch"
MODEL_TASK_CANCEL = "task.cancel"
MODEL_TASK_LIST = "task.list"
MODEL_TASK_PAUSE = "task.pause"
MODEL_TASK_RESUME = "task.resume"
MODEL_TASK_SHOW = "task.show"
MODEL_GITHUB_LIST_PRS = "github.list_prs"
MODEL_GITHUB_FETCH_PR = "github.fetch_pr"
MODEL_GITHUB_FETCH_DIFF = "github.fetch_diff"
MODEL_GITHUB_FETCH_COMMENTS = "github.fetch_comments"
MODEL_GITHUB_FETCH_CHECKS = "github.fetch_checks"
MODEL_GITHUB_COMMIT_FILES = "github.commit_files"
MODEL_GITHUB_OPEN_PR = "github.open_pr"
MODEL_GITHUB_POST_PR_REVIEW = "github.post_pr_review"
MODEL_GITHUB_POST_PR_COMMENT = "github.post_pr_comment"
MODEL_TASK_DELEGATE = "task.delegate"
MODEL_AGENT_LIST = "agent.list"
MODEL_AGENT_GET = "agent.get"

ALL_MODEL_TOOL_IDS: tuple[str, ...] = (
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_READ_RANGE,
    MODEL_FILE_WRITE,
    MODEL_FILE_FIND,
    MODEL_FILE_TRASH,
    MODEL_FILE_SEARCH,
    MODEL_FILE_EDIT,
    MODEL_CODE_PATCH,
    MODEL_CODE_GREP,
    MODEL_CODE_REPO_MAP,
    MODEL_CODE_REPO_INDEX,
    MODEL_CODE_SYMBOL_FIND,
    MODEL_TOOL_LIST,
    MODEL_TOOL_GET,
    MODEL_TOOL_AUTHOR,
    MODEL_TOOL_INSPECT,
    MODEL_TOOL_REGISTER,
    MODEL_EXEC_RUN,
    MODEL_EXEC_POLL,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_CLEAR,
    MODEL_EXEC_PASTE,
    MODEL_EXEC_SEND_KEYS,
    MODEL_EXEC_SUBMIT,
    MODEL_WEB_SEARCH,
    MODEL_WEB_FETCH,
    MODEL_WEATHER,
    MODEL_TIME,
    MODEL_LOCATION,
    MODEL_HOST_METRICS,
    MODEL_IP_PUBLIC,
    MODEL_IP_LOCAL,
    MODEL_BROWSER,
    *OPS_MODEL_TOOL_IDS,
    MODEL_GWS_CALL,
    MODEL_GWS_SCHEMA,
    MODEL_GWS_AUTH_SETUP,
    MODEL_GWS_AUTH_LOGIN,
    MODEL_GWS_AUTH_EXPORT,
    MODEL_SKILL_INGEST,
    MODEL_SKILL_INGEST_URL,
    MODEL_SKILL_INSPECT,
    MODEL_SKILL_LIST,
    MODEL_SKILL_GET,
    MODEL_SKILL_REMOVE,
    MODEL_MEMORY_WRITE,
    MODEL_MEMORY_SEARCH,
    MODEL_MEMORY_FORGET,
    MODEL_GIT_STATUS,
    MODEL_GIT_DIFF,
    MODEL_GIT_LOG,
    MODEL_GIT_SHOW,
    MODEL_GIT_BLAME,
    MODEL_GIT_BRANCH,
    MODEL_GIT_CHECKOUT,
    MODEL_GIT_ADD,
    MODEL_GIT_COMMIT,
    MODEL_GIT_STASH,
    MODEL_GIT_RESET,
    MODEL_GIT_REFLOG,
    MODEL_PLAN_SET,
    MODEL_PLAN_ADD,
    MODEL_PLAN_UPDATE,
    MODEL_PLAN_COMPLETE,
    MODEL_PLAN_LIST,
    MODEL_PLAN_CLEAR,
    MODEL_TODO_WRITE,
    MODEL_TASK_SCHEDULE,
    MODEL_TASK_CONSOLIDATE_MEMORY,
    MODEL_TASK_WATCH,
    MODEL_TASK_CANCEL,
    MODEL_TASK_LIST,
    MODEL_TASK_PAUSE,
    MODEL_TASK_RESUME,
    MODEL_TASK_SHOW,
    MODEL_GITHUB_LIST_PRS,
    MODEL_GITHUB_FETCH_PR,
    MODEL_GITHUB_FETCH_DIFF,
    MODEL_GITHUB_FETCH_COMMENTS,
    MODEL_GITHUB_FETCH_CHECKS,
    MODEL_GITHUB_COMMIT_FILES,
    MODEL_GITHUB_OPEN_PR,
    MODEL_GITHUB_POST_PR_REVIEW,
    MODEL_GITHUB_POST_PR_COMMENT,
    MODEL_TASK_DELEGATE,
    MODEL_AGENT_LIST,
    MODEL_AGENT_GET,
)

ALL_MODEL_TOOL_IDS_SET = frozenset(ALL_MODEL_TOOL_IDS)

_DYNAMIC_MODEL_TOOL_PREFIXES: tuple[str, ...] = ("mcp.",)


def is_valid_model_tool_id(model_tool_id: str) -> bool:
    token = str(model_tool_id or "").strip()
    if not token:
        return False
    if token in ALL_MODEL_TOOL_IDS_SET:
        return True
    return any(token.startswith(prefix) for prefix in _DYNAMIC_MODEL_TOOL_PREFIXES)
