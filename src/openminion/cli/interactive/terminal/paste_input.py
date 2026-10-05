"""Keep unbracketed multiline terminal paste in one composer draft."""

import time
from typing import TextIO

from prompt_toolkit.input.vt100 import Vt100Input
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys


PASTE_CONTINUATION_SECONDS = 0.1


class FocusVt100Input(Vt100Input):
    """Treat a rapid CR continuation as paste; an isolated CR remains Enter."""

    continuation_seconds = PASTE_CONTINUATION_SECONDS

    def __init__(self, stdin: TextIO) -> None:
        super().__init__(stdin)
        self._pending_return: KeyPress | None = None
        self._pending_return_at = 0.0
        self._plain_paste = False

    def read_keys(self) -> list[KeyPress]:
        keys = super().read_keys()
        if self._pending_return is not None and keys:
            continuing_paste = (
                time.monotonic() - self._pending_return_at < PASTE_CONTINUATION_SECONDS
                and (
                    (len(keys[0].data) == 1 and keys[0].data.isprintable())
                    or keys[0].key in {Keys.ControlJ, Keys.ControlM}
                )
            )
            if continuing_paste:
                keys.insert(0, KeyPress(Keys.ControlJ, "\n"))
                if keys[1].key == Keys.ControlJ:
                    del keys[1]
                self._plain_paste = True
            else:
                keys.insert(0, self._pending_return)
            self._pending_return = None

        if not keys:
            return keys
        if not all(
            len(key.data) == 1 and (key.data.isprintable() or key.data in "\r\n\t")
            for key in keys
        ):
            return keys

        returns = [index for index, key in enumerate(keys) if key.key == Keys.ControlM]
        if not returns:
            return keys
        if self._plain_paste and len(keys) > 1:
            return [KeyPress(Keys.BracketedPaste, "".join(key.data for key in keys))]
        if any(
            any(key.data.isprintable() for key in keys[index + 1 :])
            for index in returns
        ):
            self._plain_paste = True
            return [KeyPress(Keys.BracketedPaste, "".join(key.data for key in keys))]

        if returns[-1] == len(keys) - 1:
            self._pending_return_at = time.monotonic()
            self._pending_return = keys.pop()
        return keys

    def flush_keys(self) -> list[KeyPress]:
        keys = super().flush_keys()
        if self._pending_return is not None:
            keys.insert(0, self._pending_return)
            self._pending_return = None
        self._plain_paste = False
        return keys
