# ruff: noqa: F401,F403

from ._app import *  # re-export the public CLI surface
from ._app import __all__  # preserve the curated public surface list


if __name__ == "__main__":  # pragma: no cover - module-as-script execution
    raise SystemExit(main())  # type: ignore[name-defined]  # noqa: F405
