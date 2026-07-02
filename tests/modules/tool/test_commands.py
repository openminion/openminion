import pytest

from openminion.modules.tool.commands import normalize_cd_prefixed_command


@pytest.mark.parametrize(
    ("raw", "input_workdir", "expected_command", "expected_workdir"),
    (
        (
            "cd /tmp/example && python -m pytest",
            None,
            "python -m pytest",
            "/tmp/example",
        ),
        ("cd /tmp/example && pwd", "/repo", "pwd", "/repo"),
        ('cd "/tmp/example project" && ls', None, "ls", "/tmp/example project"),
        ("python -V && python -m pytest", None, "python -V && python -m pytest", None),
        ('cd "/tmp/example && pwd', None, 'cd "/tmp/example && pwd', None),
    ),
)
def test_normalize_cd_prefixed_command(
    raw: str,
    input_workdir: str | None,
    expected_command: str,
    expected_workdir: str | None,
) -> None:
    command, workdir = normalize_cd_prefixed_command(
        command=raw,
        workdir=input_workdir,
    )

    assert command == expected_command
    assert workdir == expected_workdir
