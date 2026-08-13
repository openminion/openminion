from __future__ import annotations


def copy_to_clipboard(text: str) -> bool:
    import subprocess

    payload = text.encode("utf-8", errors="replace")
    for command in (
        ("pbcopy",),
        ("wl-copy",),
        ("xclip", "-selection", "clipboard"),
        ("xsel", "--clipboard", "--input"),
        ("clip.exe",),
    ):
        try:
            completed = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                timeout=3,
                check=False,
            )
        except Exception:
            continue
        if completed.returncode == 0:
            return True
    return False


__all__ = ["copy_to_clipboard"]
