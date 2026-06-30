RUNTIME_FILE_LIST_DIR = "runtime.file.list_dir"
RUNTIME_FILE_READ = "runtime.file.read"
RUNTIME_FILE_READ_RANGE = "runtime.file.read_range"
RUNTIME_FILE_WRITE = "runtime.file.write"
RUNTIME_FILE_FIND = "runtime.file.find"
RUNTIME_FILE_TRASH = "runtime.file.trash"
RUNTIME_FILE_SEARCH = "runtime.file.search"
RUNTIME_FILE_EDIT = "runtime.file.edit"

RUNTIME_CODE_PATCH = "runtime.code.patch"
RUNTIME_CODE_GREP = "runtime.code.grep"
RUNTIME_CODE_REPO_MAP = "runtime.code.repo_map"
RUNTIME_CODE_REPO_INDEX = "runtime.code.repo_index"
RUNTIME_CODE_SYMBOL_FIND = "runtime.code.symbol_find"

RUNTIME_TOOL_LIST = "runtime.tool.list"
RUNTIME_TOOL_SEARCH = (
    "runtime.tool.search"  # compatibility alias; prefer RUNTIME_TOOL_LIST
)
RUNTIME_TOOL_GET = "runtime.tool.get"
RUNTIME_TOOL_AUTHOR = "runtime.tool.author"
RUNTIME_TOOL_INSPECT = "runtime.tool.inspect"
RUNTIME_TOOL_REGISTER = "runtime.tool.register"

RUNTIME_EXEC_RUN = "runtime.exec.run"
RUNTIME_EXEC_POLL = "runtime.exec.poll"
RUNTIME_EXEC_KILL = "runtime.exec.kill"
RUNTIME_EXEC_LIST = "runtime.exec.list"
RUNTIME_EXEC_CLEAR = "runtime.exec.clear"
RUNTIME_EXEC_PASTE = "runtime.exec.paste"
RUNTIME_EXEC_SEND_KEYS = "runtime.exec.send_keys"
RUNTIME_EXEC_SUBMIT = "runtime.exec.submit"

RUNTIME_WEB_SEARCH = "runtime.web.search"
RUNTIME_WEB_FETCH = "runtime.web.fetch"
RUNTIME_WEATHER_CURRENT = "runtime.weather.current"
RUNTIME_TIME_NOW = "runtime.time.now"
RUNTIME_LOCATION = "runtime.location"
RUNTIME_HOST_METRICS = "runtime.host.metrics"
RUNTIME_IP_PUBLIC = "runtime.ip.public"
RUNTIME_IP_LOCAL = "runtime.ip.local"
RUNTIME_BROWSER = "runtime.browser"

RUNTIME_GWS_CALL = "runtime.gws.call"
RUNTIME_GWS_SCHEMA = "runtime.gws.schema"
RUNTIME_GWS_AUTH_SETUP = "runtime.gws.auth.setup"
RUNTIME_GWS_AUTH_LOGIN = "runtime.gws.auth.login"
RUNTIME_GWS_AUTH_EXPORT = "runtime.gws.auth.export"

RUNTIME_SKILL_INGEST = "runtime.skill.ingest"
RUNTIME_SKILL_INGEST_URL = "runtime.skill.ingest_url"
RUNTIME_SKILL_INSPECT = "runtime.skill.inspect"
RUNTIME_SKILL_LIST = "runtime.skill.list"
RUNTIME_SKILL_GET = "runtime.skill.get"
RUNTIME_SKILL_REMOVE = "runtime.skill.remove"
RUNTIME_MEMORY_WRITE = "runtime.memory.write"
RUNTIME_MEMORY_SEARCH = "runtime.memory.search"
RUNTIME_MEMORY_FORGET = "runtime.memory.forget"
RUNTIME_GIT_STATUS = "runtime.git.status"
RUNTIME_GIT_DIFF = "runtime.git.diff"
RUNTIME_GIT_LOG = "runtime.git.log"
RUNTIME_GIT_SHOW = "runtime.git.show"
RUNTIME_GIT_BLAME = "runtime.git.blame"
RUNTIME_GIT_BRANCH = "runtime.git.branch"
RUNTIME_GIT_CHECKOUT = "runtime.git.checkout"
RUNTIME_GIT_ADD = "runtime.git.add"
RUNTIME_GIT_COMMIT = "runtime.git.commit"
RUNTIME_GIT_STASH = "runtime.git.stash"
RUNTIME_GIT_RESET = "runtime.git.reset"
RUNTIME_GIT_REFLOG = "runtime.git.reflog"

RUNTIME_PLAN_SET = "runtime.plan.set"
RUNTIME_PLAN_ADD = "runtime.plan.add"
RUNTIME_PLAN_UPDATE = "runtime.plan.update"
RUNTIME_PLAN_COMPLETE = "runtime.plan.complete"
RUNTIME_PLAN_LIST = "runtime.plan.list"
RUNTIME_PLAN_CLEAR = "runtime.plan.clear"
RUNTIME_TODO_WRITE = "runtime.todo.write"
RUNTIME_TASK_SCHEDULE = "runtime.task.schedule"
RUNTIME_TASK_CONSOLIDATE_MEMORY = "runtime.task.consolidate_memory"
RUNTIME_TASK_WATCH = "runtime.task.watch"
RUNTIME_TASK_CANCEL = "runtime.task.cancel"
RUNTIME_TASK_LIST = "runtime.task.list"
RUNTIME_TASK_PAUSE = "runtime.task.pause"
RUNTIME_TASK_RESUME = "runtime.task.resume"
RUNTIME_TASK_SHOW = "runtime.task.show"
RUNTIME_GITHUB_LIST_PRS = "runtime.github.list_prs"
RUNTIME_GITHUB_FETCH_PR = "runtime.github.fetch_pr"
RUNTIME_GITHUB_FETCH_DIFF = "runtime.github.fetch_diff"
RUNTIME_GITHUB_FETCH_COMMENTS = "runtime.github.fetch_comments"
RUNTIME_GITHUB_FETCH_CHECKS = "runtime.github.fetch_checks"
RUNTIME_GITHUB_COMMIT_FILES = "runtime.github.commit_files"
RUNTIME_GITHUB_OPEN_PR = "runtime.github.open_pr"
RUNTIME_GITHUB_POST_PR_REVIEW = "runtime.github.post_pr_review"
RUNTIME_GITHUB_POST_PR_COMMENT = "runtime.github.post_pr_comment"
# task / agent delegation tool family
RUNTIME_TASK_DELEGATE = "runtime.task.delegate"
RUNTIME_AGENT_LIST = "runtime.agent.list"
RUNTIME_AGENT_GET = "runtime.agent.get"

ALL_RUNTIME_BINDING_IDS: tuple[str, ...] = (
    RUNTIME_FILE_LIST_DIR,
    RUNTIME_FILE_READ,
    RUNTIME_FILE_READ_RANGE,
    RUNTIME_FILE_WRITE,
    RUNTIME_FILE_FIND,
    RUNTIME_FILE_TRASH,
    RUNTIME_FILE_SEARCH,
    RUNTIME_FILE_EDIT,
    RUNTIME_CODE_PATCH,
    RUNTIME_CODE_GREP,
    RUNTIME_CODE_REPO_MAP,
    RUNTIME_CODE_REPO_INDEX,
    RUNTIME_CODE_SYMBOL_FIND,
    RUNTIME_TOOL_LIST,
    RUNTIME_TOOL_GET,
    RUNTIME_TOOL_AUTHOR,
    RUNTIME_TOOL_INSPECT,
    RUNTIME_TOOL_REGISTER,
    RUNTIME_EXEC_RUN,
    RUNTIME_EXEC_POLL,
    RUNTIME_EXEC_KILL,
    RUNTIME_EXEC_LIST,
    RUNTIME_EXEC_CLEAR,
    RUNTIME_EXEC_PASTE,
    RUNTIME_EXEC_SEND_KEYS,
    RUNTIME_EXEC_SUBMIT,
    RUNTIME_WEB_SEARCH,
    RUNTIME_WEB_FETCH,
    RUNTIME_WEATHER_CURRENT,
    RUNTIME_TIME_NOW,
    RUNTIME_LOCATION,
    RUNTIME_HOST_METRICS,
    RUNTIME_IP_PUBLIC,
    RUNTIME_IP_LOCAL,
    RUNTIME_BROWSER,
    RUNTIME_GWS_CALL,
    RUNTIME_GWS_SCHEMA,
    RUNTIME_GWS_AUTH_SETUP,
    RUNTIME_GWS_AUTH_LOGIN,
    RUNTIME_GWS_AUTH_EXPORT,
    RUNTIME_SKILL_INGEST,
    RUNTIME_SKILL_INGEST_URL,
    RUNTIME_SKILL_INSPECT,
    RUNTIME_SKILL_LIST,
    RUNTIME_SKILL_GET,
    RUNTIME_SKILL_REMOVE,
    RUNTIME_MEMORY_WRITE,
    RUNTIME_MEMORY_SEARCH,
    RUNTIME_MEMORY_FORGET,
    RUNTIME_GIT_STATUS,
    RUNTIME_GIT_DIFF,
    RUNTIME_GIT_LOG,
    RUNTIME_GIT_SHOW,
    RUNTIME_GIT_BLAME,
    RUNTIME_GIT_BRANCH,
    RUNTIME_GIT_CHECKOUT,
    RUNTIME_GIT_ADD,
    RUNTIME_GIT_COMMIT,
    RUNTIME_GIT_STASH,
    RUNTIME_GIT_RESET,
    RUNTIME_GIT_REFLOG,
    RUNTIME_PLAN_SET,
    RUNTIME_PLAN_ADD,
    RUNTIME_PLAN_UPDATE,
    RUNTIME_PLAN_COMPLETE,
    RUNTIME_PLAN_LIST,
    RUNTIME_PLAN_CLEAR,
    RUNTIME_TODO_WRITE,
    RUNTIME_TASK_SCHEDULE,
    RUNTIME_TASK_CONSOLIDATE_MEMORY,
    RUNTIME_TASK_WATCH,
    RUNTIME_TASK_CANCEL,
    RUNTIME_TASK_LIST,
    RUNTIME_TASK_PAUSE,
    RUNTIME_TASK_RESUME,
    RUNTIME_TASK_SHOW,
    RUNTIME_GITHUB_LIST_PRS,
    RUNTIME_GITHUB_FETCH_PR,
    RUNTIME_GITHUB_FETCH_DIFF,
    RUNTIME_GITHUB_FETCH_COMMENTS,
    RUNTIME_GITHUB_FETCH_CHECKS,
    RUNTIME_GITHUB_COMMIT_FILES,
    RUNTIME_GITHUB_OPEN_PR,
    RUNTIME_GITHUB_POST_PR_REVIEW,
    RUNTIME_GITHUB_POST_PR_COMMENT,
    RUNTIME_TASK_DELEGATE,
    RUNTIME_AGENT_LIST,
    RUNTIME_AGENT_GET,
)

ALL_RUNTIME_BINDING_IDS_SET = frozenset(ALL_RUNTIME_BINDING_IDS)

_DYNAMIC_RUNTIME_BINDING_PREFIXES: tuple[str, ...] = ("runtime.mcp.",)


def is_valid_runtime_binding_id(runtime_binding_id: str) -> bool:
    token = str(runtime_binding_id or "").strip()
    if not token:
        return False
    if token in ALL_RUNTIME_BINDING_IDS_SET:
        return True
    return any(token.startswith(prefix) for prefix in _DYNAMIC_RUNTIME_BINDING_PREFIXES)
