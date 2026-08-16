from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _add_commands(subparsers: argparse._SubParsersAction) -> None:
    health_parser = subparsers.add_parser(
        "health",
        help="Check sample module health",
    )
    health_parser.set_defaults(func=sample_health)

    test_parser = subparsers.add_parser(
        "test",
        help="Test sample provider with input",
    )
    test_parser.add_argument(
        "--provider",
        default="default",
        help="Provider ID to test",
    )
    test_parser.add_argument(
        "--input",
        default="test",
        help="Input data to process",
    )
    test_parser.set_defaults(func=sample_test)

    list_parser = subparsers.add_parser(
        "list",
        help="List available sample providers",
    )
    list_parser.set_defaults(func=sample_list)


def sample_health(_args: argparse.Namespace) -> dict[str, Any]:
    from .service import SampleServiceImpl
    from openminion.modules.base import ModuleDescriptor

    descriptor = ModuleDescriptor(
        name="sample",
        version="1.0.0",
    )
    return SampleServiceImpl(descriptor=descriptor).healthcheck()


def sample_test(args: argparse.Namespace) -> dict[str, Any]:
    from openminion.modules.providers import ProviderNotFoundError

    from .provider import (
        create_sample_provider_registry,
        get_sample_provider,
    )

    registry = create_sample_provider_registry()

    try:
        provider = get_sample_provider(
            registry,
            provider_id=args.provider,
            config={},
        )
    except ProviderNotFoundError as exc:
        return {"success": False, "error": str(exc)}
    return provider.process(args.input)


def sample_list(_args: argparse.Namespace) -> dict[str, Any]:
    from .provider import create_sample_provider_registry

    registry = create_sample_provider_registry()
    providers = registry.list_providers()
    return {
        "providers": providers,
        "count": len(providers),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sample",
        description="Sample module CLI",
    )
    parser.set_defaults(func=None)
    _add_commands(parser.add_subparsers(dest="sample_command"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.func is None:
        parser.print_help()
        return 0
    result = args.func(args)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return int(result.get("success") is False)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
