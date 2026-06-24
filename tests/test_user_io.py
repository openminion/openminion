from __future__ import annotations

from io import StringIO

from openminion.base.user_io import UserIO


def test_user_io_writes_stdout_and_stderr() -> None:
    out = StringIO()
    err = StringIO()
    io = UserIO(stdout=out, stderr=err)

    io.out("hello")
    io.err("warn")
    io.blank()

    assert out.getvalue() == "hello\n\n"
    assert err.getvalue() == "warn\n"


def test_user_io_json_output() -> None:
    out = StringIO()
    io = UserIO(stdout=out)

    io.json({"b": 2, "a": 1})

    assert out.getvalue() == '{\n  "a": 1,\n  "b": 2\n}\n'
