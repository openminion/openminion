from __future__ import annotations

import openminion.tools.exec.process as process
from openminion.tools.exec.process import ShellFamily


def test_select_shell_windows_prefers_powershell(monkeypatch) -> None:
    monkeypatch.setattr(process, "_is_windows_platform", lambda: True)

    def _fake_which(candidate: str) -> str | None:
        if candidate == "pwsh":
            return r"C:\Program Files\PowerShell\7\pwsh.exe"
        return None

    monkeypatch.setattr(process.shutil, "which", _fake_which)

    argv, shell_family = process._select_shell("echo hi")

    assert shell_family == ShellFamily.POWERSHELL
    assert argv == [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        "-NoLogo",
        "-NoProfile",
        "-Command",
        "echo hi",
    ]


def test_select_shell_windows_falls_back_to_cmd(monkeypatch) -> None:
    monkeypatch.setattr(process, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(process.shutil, "which", lambda _candidate: None)

    argv, shell_family = process._select_shell("echo hi")

    assert shell_family == ShellFamily.CMD
    assert argv == ["cmd.exe", "/c", "echo hi"]


def test_resolve_shell_family_uses_selector(monkeypatch) -> None:
    monkeypatch.setattr(
        process,
        "_select_shell",
        lambda _command: (["dummy"], ShellFamily.CMD),
    )

    assert process.resolve_shell_family() == ShellFamily.CMD
