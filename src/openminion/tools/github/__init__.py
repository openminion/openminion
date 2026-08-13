from typing import Any

from .registrar import REGISTRAR as _REGISTRAR, GithubRegistrar

REGISTRAR: GithubRegistrar = _REGISTRAR


def register(*args: Any, **kwargs: Any) -> Any:
    from .plugin import register as register_impl

    return register_impl(*args, **kwargs)


def register_provider(*args: Any, **kwargs: Any) -> Any:
    from .providers import register_provider as register_provider_impl

    return register_provider_impl(*args, **kwargs)


def create_rest_provider(*args: Any, **kwargs: Any) -> Any:
    from .rest import GithubRestProvider

    return GithubRestProvider(*args, **kwargs)


__all__ = ["REGISTRAR", "register", "register_provider", "create_rest_provider"]
