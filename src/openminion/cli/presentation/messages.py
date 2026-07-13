from __future__ import annotations

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text

from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.markers import token_rich_style

_MARKDOWN_PREFIXES = ("#", "- ", "* ", "> ", "```", "1.", "|")
_USER_PREFIX_STYLE = token_rich_style(StyleToken.USER, dim=True)
_MUTED_BASE = token_rich_style(StyleToken.MUTED)
_SYSTEM_STYLE = f"italic {_MUTED_BASE}" if _MUTED_BASE else "italic"
_ERROR_STYLE = token_rich_style(StyleToken.ERROR)


def looks_like_markdown(text: str) -> bool:
    sample = str(text or "").strip()
    return bool(sample.startswith(_MARKDOWN_PREFIXES) or "```" in sample)


def render_markdown(text: str) -> RichMarkdown:
    return RichMarkdown(
        str(text or ""),
        code_theme="monokai",
        inline_code_lexer="text",
        justify="left",
    )


def render_body(text: str, *, markdown_allowed: bool = True) -> object:
    body = str(text or "")
    if markdown_allowed and body and looks_like_markdown(body):
        return render_markdown(body)
    return Text(body)


def render_user_text(text: str) -> Text:
    body = Text()
    body.append("> ", style=_USER_PREFIX_STYLE)
    body.append(str(text or ""))
    return body


def render_system_text(text: str) -> Text:
    return Text(str(text or ""), style=_SYSTEM_STYLE)


def render_error_text(text: str) -> Text:
    return Text(str(text or ""), style=_ERROR_STYLE)


__all__ = [
    "looks_like_markdown",
    "render_body",
    "render_error_text",
    "render_markdown",
    "render_system_text",
    "render_user_text",
]
