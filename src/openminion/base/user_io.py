import json
import sys
from dataclasses import dataclass
from typing import Any, TextIO


@dataclass(frozen=True)
class UserIO:
    """Explicit user-output surface kept separate from operational logging."""

    stdout: TextIO = sys.stdout
    stderr: TextIO = sys.stderr

    def out(self, message: str = "", *, end: str = "\n", flush: bool = False) -> None:
        print(str(message), file=self.stdout, end=end, flush=flush)

    def err(self, message: str = "", *, end: str = "\n", flush: bool = False) -> None:
        print(str(message), file=self.stderr, end=end, flush=flush)

    def json(
        self,
        payload: Any,
        *,
        indent: int = 2,
        sort_keys: bool = True,
        to_stderr: bool = False,
    ) -> None:
        stream = self.stderr if to_stderr else self.stdout
        print(
            json.dumps(payload, indent=indent, sort_keys=sort_keys),
            file=stream,
        )

    def blank(self) -> None:
        print("", file=self.stdout)
