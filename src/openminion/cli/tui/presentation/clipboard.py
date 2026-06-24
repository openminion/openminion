from __future__ import annotations


def copy_to_clipboard(text: str) -> bool:
    import platform
    import shutil
    import subprocess

    system = platform.system()
    try:
        if system == "Darwin" and shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=text.encode(), check=True, timeout=2)
            return True
        if system == "Linux":
            for command in (
                ("xclip", "-selection", "clipboard"),
                ("xsel", "--clipboard", "--input"),
            ):
                if shutil.which(command[0]):
                    subprocess.run(command, input=text.encode(), check=True, timeout=2)
                    return True
        if system == "Windows" and shutil.which("clip"):
            subprocess.run(["clip"], input=text.encode(), check=True, timeout=2)
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


__all__ = ["copy_to_clipboard"]
