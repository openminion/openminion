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
SPINNER_FRAMES: tuple[str, ...] = (
    "⠋",
    "⠙",
    "⠹",
    "⠸",
    "⠼",
    "⠴",
    "⠦",
    "⠧",
    "⠇",
    "⠏",
)


def format_elapsed_label(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s"


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

    def current_frame(self, now: float) -> str:
        if self._plain:
            return ""
        elapsed = max(0.0, float(now) - self._start)
        index = int(elapsed * 10) % len(SPINNER_FRAMES)
        return SPINNER_FRAMES[index]

    def elapsed_label(self, now: float) -> str:
        return format_elapsed_label(float(now) - self._start)


def format_status_row(
    verb: str,
    elapsed: str,
    hint: str = "esc to interrupt",
    *,
    plain: bool = False,
    status_label: str = "",
    spinner_frame: str = "",
) -> Text:
    text = Text(style="dim")
    text.append("(")
    label = str(status_label or "").strip()
    frame = str(spinner_frame or "").strip() or "✻"
    if not plain:
        text.append(f"{frame} ", style="dim italic")
    if label:
        text.append(label)
        text.append(" · ")
    elif not plain and verb:
        text.append(verb)
        text.append(" · ")
    text.append(elapsed)
    if hint:
        text.append(" · ")
        text.append(hint, style="dim italic")
    text.append(")")
    return text


__all__ = [
    "SPINNER_FRAMES",
    "Spinner",
    "THINKING_VERB",
    "VERBS",
    "format_elapsed_label",
    "format_status_row",
]
