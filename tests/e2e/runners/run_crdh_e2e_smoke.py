#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CRDH E2E smoke test for Cortensor result delivery"
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to agent configuration file",
    )
    parser.add_argument(
        "--agent",
        default="cortensor35",
        help="Agent ID to use for test",
    )
    parser.add_argument(
        "--session",
        default="crdh-e2e-test",
        help="Session ID for test",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Maximum wait time in seconds (default: 300)",
    )
    parser.add_argument(
        "--prompt",
        default="what's weather at sf?",
        help="Test prompt to send (default: weather query)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    return parser.parse_args()


async def run_e2e_test(
    *,
    config_path: str,
    agent_id: str,
    session_id: str,
    prompt: str,
    timeout_seconds: int,
    verbose: bool,
) -> dict:
    import logging

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
    from openminion.base.config import load_config, resolve_agent_config
    from openminion.modules.llm.providers.base import ProviderRequest
    from openminion.modules.llm.providers.factory import build_provider

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("crdh.e2e")

    start_time = time.time()
    result = {
        "success": False,
        "duration_seconds": 0.0,
        "response_text": "",
        "error": "",
        "stage": "",
    }

    try:
        # Load configuration and resolve the requested agent profile.
        config = load_config(config_path)
        agent_config = resolve_agent_config(config, agent_id=agent_id)
        effective_config = replace(config, agent=agent_config)
        provider_name = (
            (
                effective_config.agents[
                    next(iter(effective_config.agents.keys()))
                ].provider
                or ""
            )
            .strip()
            .lower()
        )
        if provider_name != "cortensor":
            raise RuntimeError(
                f"CRDH smoke test expects a cortensor provider, got '{provider_name or 'unknown'}'."
            )

        provider = build_provider(effective_config, logger)
        provider_cfg = effective_config.providers.cortensor

        # Build request
        request = ProviderRequest(
            user_message=prompt,
            system_prompt=effective_config.agents[
                next(iter(effective_config.agents.keys()))
            ].system_prompt,
            history=[],
            tools=[],
            metadata={
                "result_wait_attempts": str(
                    max(int(provider_cfg.result_wait_attempts), 1)
                ),
                "result_wait_interval_seconds": str(
                    max(float(provider_cfg.result_wait_interval_seconds), 0.1)
                ),
                "session_id": session_id,
            },
        )

        # Execute with timeout
        logger.info(f"Starting E2E test with {timeout_seconds}s timeout")
        logger.info(f"Prompt: {prompt}")

        try:
            response = await asyncio.wait_for(
                provider.generate(request),
                timeout=timeout_seconds,
            )

            duration = time.time() - start_time
            result["duration_seconds"] = duration
            result["success"] = True
            result["response_text"] = response.text[:500]  # Truncate for logging
            result["stage"] = "delivered"

            logger.info(f"Success after {duration:.1f}s")
            logger.info(f"Response: {response.text[:200]}...")
            logger.info(f"Model: {response.model}")
            logger.info(f"Finish reason: {response.finish_reason}")

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            result["duration_seconds"] = duration
            result["error"] = f"Request timed out after {duration:.1f}s"
            result["stage"] = "timeout"

            logger.error(f"TIMEOUT: No response after {duration:.1f}s")

    except Exception as exc:
        duration = time.time() - start_time
        result["duration_seconds"] = duration
        result["error"] = f"Test execution failed: {exc}"
        result["stage"] = "error"
        logger.exception("E2E test failed with exception")

    return result


def main() -> int:
    args = parse_args()

    print("=== CRDH-07 E2E Smoke Test ===")
    print(f"Config: {args.config}")
    print(f"Agent: {args.agent}")
    print(f"Session: {args.session}")
    print(f"Timeout: {args.timeout}s")
    print(f"Prompt: {args.prompt}")
    print()

    # Run test
    result = asyncio.run(
        run_e2e_test(
            config_path=args.config,
            agent_id=args.agent,
            session_id=args.session,
            prompt=args.prompt,
            timeout_seconds=args.timeout,
            verbose=args.verbose,
        )
    )

    # Report results
    print()
    print("=== Results ===")
    print(f"Success: {result['success']}")
    print(f"Duration: {result['duration_seconds']:.1f}s")
    print(f"Final stage: {result['stage']}")

    if result["success"]:
        print(f"Response: {result['response_text'][:200]}...")
        print()
        print("✓ E2E test PASSED - response received within timeout")
        return 0
    print(f"Error: {result['error']}")
    print()
    print("✗ E2E test FAILED - no response within expected duration")

    # Provide guidance based on stage
    if result["stage"] == "timeout":
        print("  → Turn-level watchdog triggered")
        print("  → Request may still be processing on Cortensor network")

    return 1
