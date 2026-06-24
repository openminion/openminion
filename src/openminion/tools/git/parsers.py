from dataclasses import dataclass
from typing import Any


# git branch (plain list)


@dataclass
class BranchEntry:
    """Single line of `git branch` output."""

    name: str
    is_current: bool


def parse_branch_list(stdout: str) -> list[BranchEntry]:
    """Parse `git branch` stdout.

    Each line is either ``* <name>`` (current branch) or ``  <name>``
    (other branch). Whitespace around the name is stripped.
    """

    entries: list[BranchEntry] = []
    for line in stdout.splitlines():
        if not line:
            continue
        is_current = line.startswith("* ")
        name = line[2:].strip()
        if not name:
            continue
        entries.append(BranchEntry(name=name, is_current=is_current))
    return entries


def branch_list_to_dict(entries: list[BranchEntry]) -> list[dict[str, Any]]:
    return [{"name": entry.name, "is_current": entry.is_current} for entry in entries]


# git reflog


@dataclass
class ReflogEntry:
    """A single line of `git reflog` (default format)."""

    sha: str
    ref: str
    action: str
    summary: str


def parse_reflog_output(stdout: str) -> list[ReflogEntry]:
    entries: list[ReflogEntry] = []
    for line in stdout.splitlines():
        if not line:
            continue
        # Format: `<sha> HEAD@{N}: <action>: <summary>` — split on first space
        # to peel off the sha, then on the first `: ` for the action/summary.
        space_idx = line.find(" ")
        if space_idx == -1:
            continue
        sha = line[:space_idx]
        rest = line[space_idx + 1 :]
        colon_idx = rest.find(": ")
        if colon_idx == -1:
            continue
        ref = rest[:colon_idx]
        body = rest[colon_idx + 2 :]
        # Body is `<action>: <summary>`; some entries have no extra colon
        # (e.g. `commit (initial)`) so partition fallback to whole-body action.
        body_colon = body.find(": ")
        if body_colon == -1:
            action = body
            summary = ""
        else:
            action = body[:body_colon]
            summary = body[body_colon + 2 :]
        entries.append(
            ReflogEntry(
                sha=sha,
                ref=ref,
                action=action,
                summary=summary,
            )
        )
    return entries


def reflog_to_dict(entries: list[ReflogEntry]) -> list[dict[str, Any]]:
    return [
        {
            "sha": entry.sha,
            "ref": entry.ref,
            "action": entry.action,
            "summary": entry.summary,
        }
        for entry in entries
    ]


# git stash list


@dataclass
class StashEntry:
    """A single line of `git stash list` output."""

    index: int
    ref: str
    branch: str
    message: str


def parse_stash_list(stdout: str) -> list[StashEntry]:
    """Parse `git stash list` output."""

    entries: list[StashEntry] = []
    for line in stdout.splitlines():
        if not line:
            continue
        if not line.startswith("stash@{"):
            continue
        # Find the closing brace; the integer between {} is the stash index.
        brace_close = line.find("}")
        if brace_close == -1:
            continue
        index_str = line[len("stash@{") : brace_close]
        try:
            index = int(index_str)
        except ValueError:
            continue
        # Everything after "stash@{N}: " is the body.
        body = line[brace_close + 1 :].lstrip(": ").rstrip()
        # The body's form is "WIP on <branch>: <message>" or "On <branch>: <message>".
        branch = ""
        message = body
        for prefix in ("WIP on ", "On "):
            if body.startswith(prefix):
                rest = body[len(prefix) :]
                colon = rest.find(":")
                if colon != -1:
                    branch = rest[:colon].strip()
                    message = rest[colon + 1 :].strip()
                break
        entries.append(
            StashEntry(
                index=index,
                ref=f"stash@{{{index}}}",
                branch=branch,
                message=message,
            )
        )
    return entries


def stash_list_to_dict(entries: list[StashEntry]) -> list[dict[str, Any]]:
    return [
        {
            "index": entry.index,
            "ref": entry.ref,
            "branch": entry.branch,
            "message": entry.message,
        }
        for entry in entries
    ]


# git status --porcelain=v2 --branch


@dataclass
class StatusEntry:
    """A single file entry in `git status --porcelain=v2 --branch` output."""

    path: str
    index_status: str
    worktree_status: str
    raw_line: str


@dataclass
class StatusOutput:
    branch: str
    upstream: str
    ahead: int
    behind: int
    files: list[StatusEntry]


def parse_status_porcelain_v2(stdout: str) -> StatusOutput:
    """Parse `git status --porcelain=v2 --branch` output."""

    branch = ""
    upstream = ""
    ahead = 0
    behind = 0
    files: list[StatusEntry] = []

    for line in stdout.splitlines():
        if not line:
            continue
        if line.startswith("# branch."):
            # The header form is `# branch.<name> <value...>` — strip the
            rest = line[len("# branch.") :]
            header, _, value = rest.partition(" ")
            header = header.strip()
            value = value.strip()
            if header == "head":
                branch = value
            elif header == "upstream":
                upstream = value
            elif header == "ab":
                # Value is `+N -M` for ahead/behind counts.
                for token in value.split():
                    if token.startswith("+"):
                        try:
                            ahead = int(token[1:])
                        except ValueError:
                            pass
                    elif token.startswith("-"):
                        try:
                            behind = int(token[1:])
                        except ValueError:
                            pass
            continue
        if line.startswith(("1 ", "2 ")):
            # Ordinary or renamed/copied file. The XY status codes are the
            # second whitespace-separated token (e.g. ".M", "M.", "RM").
            tokens = line.split(" ", 8)
            if len(tokens) < 9:
                continue
            xy = tokens[1]
            path = tokens[-1]
            index_status = xy[0] if len(xy) >= 1 else "?"
            worktree_status = xy[1] if len(xy) >= 2 else "?"
            files.append(
                StatusEntry(
                    path=path,
                    index_status=index_status,
                    worktree_status=worktree_status,
                    raw_line=line,
                )
            )
            continue
        if line.startswith("u "):
            tokens = line.split(" ", 10)
            if len(tokens) < 2:
                continue
            xy = tokens[1] if len(tokens[1]) >= 2 else "??"
            path = tokens[-1]
            files.append(
                StatusEntry(
                    path=path,
                    index_status=xy[0],
                    worktree_status=xy[1],
                    raw_line=line,
                )
            )
            continue
        if line.startswith("? "):
            path = line[2:]
            files.append(
                StatusEntry(
                    path=path,
                    index_status="?",
                    worktree_status="?",
                    raw_line=line,
                )
            )
            continue

    return StatusOutput(
        branch=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        files=files,
    )


def status_to_dict(status: StatusOutput) -> dict[str, Any]:
    return {
        "branch": status.branch,
        "upstream": status.upstream,
        "ahead": status.ahead,
        "behind": status.behind,
        "files": [
            {
                "path": entry.path,
                "index_status": entry.index_status,
                "worktree_status": entry.worktree_status,
            }
            for entry in status.files
        ],
    }


# git log --pretty=format:...

# Field separator used in our `--pretty=format` string. Chosen to be a value
# that does not appear in normal commit metadata so we can split reliably.
LOG_FIELD_SEP = "\x1f"
LOG_RECORD_SEP = "\x1e"

LOG_PRETTY_FORMAT = LOG_RECORD_SEP.join(
    [
        "%H",
        "%an",
        "%ae",
        "%aI",
        "%s",
        "%b",
    ]
).replace(LOG_RECORD_SEP, LOG_FIELD_SEP)
# The final `--pretty=format` string passed to git. Each commit is the
# above field sequence; commits are separated by ASCII RS (`\x1e`).
LOG_PRETTY_FORMAT_ARG = LOG_PRETTY_FORMAT + LOG_RECORD_SEP


@dataclass
class LogEntry:
    sha: str
    author_name: str
    author_email: str
    author_date: str
    subject: str
    body: str


def parse_log_output(stdout: str) -> list[LogEntry]:
    """Parse the stdout produced by `git log --pretty=format:LOG_PRETTY_FORMAT_ARG`."""

    entries: list[LogEntry] = []
    raw = stdout
    if not raw:
        return entries
    records = raw.split(LOG_RECORD_SEP)
    for record in records:
        record = record.strip("\n")
        if not record:
            continue
        fields = record.split(LOG_FIELD_SEP)
        if len(fields) < 6:
            # Pad missing trailing fields with empty strings — happens when
            # the commit has no body.
            fields = fields + [""] * (6 - len(fields))
        sha, author_name, author_email, author_date, subject, body = fields[:6]
        entries.append(
            LogEntry(
                sha=sha,
                author_name=author_name,
                author_email=author_email,
                author_date=author_date,
                subject=subject,
                body=body,
            )
        )
    return entries


def log_to_dict(entries: list[LogEntry]) -> list[dict[str, Any]]:
    return [
        {
            "sha": entry.sha,
            "author_name": entry.author_name,
            "author_email": entry.author_email,
            "author_date": entry.author_date,
            "subject": entry.subject,
            "body": entry.body,
        }
        for entry in entries
    ]


# git blame --porcelain


@dataclass
class BlameLine:
    line_number: int
    sha: str
    author_name: str
    author_date_unix: int
    content: str


def parse_blame_porcelain(stdout: str) -> list[BlameLine]:
    """Parse `git blame --porcelain` output into per-line records.

    The porcelain format groups header lines per commit; we track the most
    recent header set as we walk and emit one `BlameLine` per content line.
    """

    lines: list[BlameLine] = []
    sha = ""
    author_name = ""
    author_date_unix = 0
    line_number = 0

    iter_lines = iter(stdout.splitlines())
    for line in iter_lines:
        if not line:
            continue
        if line[0] in "0123456789abcdef" and len(line) >= 40 and " " in line:
            parts = line.split(" ")
            if len(parts) >= 3 and len(parts[0]) == 40:
                sha = parts[0]
                # parts[1] = original line number; parts[2] = final line number
                try:
                    line_number = int(parts[2])
                except ValueError:
                    line_number = 0
                continue
        if line.startswith("author "):
            author_name = line[len("author ") :]
            continue
        if line.startswith("author-time "):
            try:
                author_date_unix = int(line[len("author-time ") :])
            except ValueError:
                author_date_unix = 0
            continue
        if line.startswith("\t"):
            # Content line — leading TAB marks the actual file content.
            content = line[1:]
            lines.append(
                BlameLine(
                    line_number=line_number,
                    sha=sha,
                    author_name=author_name,
                    author_date_unix=author_date_unix,
                    content=content,
                )
            )
            continue

    return lines


def blame_to_dict(lines: list[BlameLine]) -> list[dict[str, Any]]:
    return [
        {
            "line_number": entry.line_number,
            "sha": entry.sha,
            "author_name": entry.author_name,
            "author_date_unix": entry.author_date_unix,
            "content": entry.content,
        }
        for entry in lines
    ]
