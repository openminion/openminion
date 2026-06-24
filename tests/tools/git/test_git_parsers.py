from __future__ import annotations

import unittest

from openminion.tools.git.parsers import (
    LOG_FIELD_SEP,
    LOG_RECORD_SEP,
    parse_blame_porcelain,
    parse_log_output,
    parse_status_porcelain_v2,
)


class StatusParserTests(unittest.TestCase):
    def test_clean_tree_returns_branch_only(self) -> None:
        stdout = (
            "# branch.oid abc123\n"
            "# branch.head main\n"
            "# branch.upstream origin/main\n"
            "# branch.ab +0 -0\n"
        )
        status = parse_status_porcelain_v2(stdout)
        self.assertEqual(status.branch, "main")
        self.assertEqual(status.upstream, "origin/main")
        self.assertEqual(status.ahead, 0)
        self.assertEqual(status.behind, 0)
        self.assertEqual(status.files, [])

    def test_modified_and_untracked_files(self) -> None:
        # Format: `1 XY <8 fields> path` for ordinary entries; `? path` for untracked.
        stdout = (
            "# branch.head feature\n"
            "1 .M N... 100644 100644 100644 abc def README.md\n"
            "1 A. N... 000000 100644 100644 000 fff src/new.py\n"
            "? notes.txt\n"
        )
        status = parse_status_porcelain_v2(stdout)
        self.assertEqual(status.branch, "feature")
        self.assertEqual(len(status.files), 3)
        paths = sorted(entry.path for entry in status.files)
        self.assertEqual(paths, ["README.md", "notes.txt", "src/new.py"])
        # The untracked entry has both statuses as `?`.
        untracked = next(entry for entry in status.files if entry.path == "notes.txt")
        self.assertEqual(untracked.index_status, "?")
        self.assertEqual(untracked.worktree_status, "?")

    def test_ahead_behind_parsed(self) -> None:
        stdout = "# branch.head main\n# branch.ab +3 -2\n"
        status = parse_status_porcelain_v2(stdout)
        self.assertEqual(status.ahead, 3)
        self.assertEqual(status.behind, 2)


class LogParserTests(unittest.TestCase):
    def _build_log_record(
        self,
        sha: str,
        author_name: str,
        author_email: str,
        author_date: str,
        subject: str,
        body: str,
    ) -> str:
        return (
            LOG_FIELD_SEP.join(
                [sha, author_name, author_email, author_date, subject, body]
            )
            + LOG_RECORD_SEP
        )

    def test_single_commit_record(self) -> None:
        stdout = self._build_log_record(
            "abc1234",
            "Alice",
            "alice@example.com",
            "2026-01-01T00:00:00Z",
            "first commit",
            "",
        )
        entries = parse_log_output(stdout)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.sha, "abc1234")
        self.assertEqual(entry.author_name, "Alice")
        self.assertEqual(entry.author_email, "alice@example.com")
        self.assertEqual(entry.subject, "first commit")
        self.assertEqual(entry.body, "")

    def test_multiple_commits(self) -> None:
        stdout = self._build_log_record(
            "aaa", "A", "a@a.com", "2026-01-01T00:00:00Z", "first", ""
        ) + self._build_log_record(
            "bbb", "B", "b@b.com", "2026-01-02T00:00:00Z", "second", "longer body"
        )
        entries = parse_log_output(stdout)
        self.assertEqual([e.sha for e in entries], ["aaa", "bbb"])
        self.assertEqual(entries[1].body, "longer body")

    def test_empty_stdout_returns_empty_list(self) -> None:
        self.assertEqual(parse_log_output(""), [])


class BlameParserTests(unittest.TestCase):
    def test_single_line(self) -> None:
        stdout = (
            "abcdef0123456789abcdef0123456789abcdef01 1 1 1\n"
            "author Alice\n"
            "author-time 1700000000\n"
            "author-tz +0000\n"
            "summary first commit\n"
            "filename README.md\n"
            "\thello world\n"
        )
        lines = parse_blame_porcelain(stdout)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line.line_number, 1)
        self.assertEqual(line.sha, "abcdef0123456789abcdef0123456789abcdef01")
        self.assertEqual(line.author_name, "Alice")
        self.assertEqual(line.author_date_unix, 1700000000)
        self.assertEqual(line.content, "hello world")

    def test_multiple_lines_same_commit(self) -> None:
        stdout = (
            "abcdef0123456789abcdef0123456789abcdef01 1 1 2\n"
            "author Alice\n"
            "author-time 1700000000\n"
            "filename README.md\n"
            "\tline one\n"
            "abcdef0123456789abcdef0123456789abcdef01 2 2\n"
            "\tline two\n"
        )
        lines = parse_blame_porcelain(stdout)
        self.assertEqual(len(lines), 2)
        self.assertEqual([line.content for line in lines], ["line one", "line two"])
        self.assertEqual([line.line_number for line in lines], [1, 2])
