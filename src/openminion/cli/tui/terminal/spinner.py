from __future__ import annotations

from rich.text import Text

VERBS: tuple[str, ...] = (
    "Cogitating",
    "Pondering",
    "Computing",
    "Brewing",
    "Mulling",
    "Reasoning",
    "Drafting",
    "Composing",
    "Considering",
    "Reflecting",
    "Synthesizing",
    "Crafting",
    "Working",
    "Thinking",
    "Distilling",
    "Inferring",
    "Arranging",
    "Resolving",
    "Tinkering",
    "Weaving",
    "Charting",
    "Plotting",
    "Pacing",
    "Settling",
    "Rallying",
    "Honing",
    "Tuning",
    "Polishing",
    "Finalizing",
    "Wrangling",
)
THINKING_VERB = "Thinking"


class Spinner:
    def __init__(
        self,
        start_time: float,
        *,
        plain: bool = False,
        rotate_seconds: float = 3.0,
    ) -> None:
        self._start = float(start_time)
        self._plain = bool(plain)
        self._rotate = max(0.1, float(rotate_seconds))

    def current_verb(self, now: float) -> str:
        if self._plain:
            return ""
        elapsed = max(0.0, float(now) - self._start)
        index = int(elapsed // self._rotate) % len(VERBS)
        return VERBS[index]

    def elapsed_label(self, now: float) -> str:
        elapsed = max(0.0, float(now) - self._start)
        if elapsed < 60:
            if elapsed < 10:
                return f"{elapsed:.1f}s"
            return f"{int(elapsed)}s"
        minutes = int(elapsed // 60)
        seconds = int(elapsed - minutes * 60)
        return f"{minutes}m{seconds:02d}s"


def format_status_row(
    verb: str,
    elapsed: str,
    hint: str = "esc to interrupt",
    *,
    plain: bool = False,
) -> Text:
    text = Text(style="dim", justify="right")
    text.append("(")
    if not plain and verb:
        text.append("✻ ", style="dim italic")
        text.append(verb)
        text.append(" · ")
    text.append(elapsed)
    if hint:
        text.append(" · ")
        text.append(hint, style="dim italic")
    text.append(")")
    return text


__all__ = ["Spinner", "THINKING_VERB", "VERBS", "format_status_row"]
