"""CLI entry point: python -m decision_analysis [--demo | --no-llm]"""

from __future__ import annotations

import argparse
import sys


def _force_utf8_stdio() -> None:
    """Windows consoles often default to a legacy codepage (GBK, cp1252),
    which mangles non-ASCII input and output. Force UTF-8 on all stdio."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

from .config import load_env  # noqa: E402

load_env()

from .orchestrator import run_demo, run_interactive, run_llm  # noqa: E402
from .provider import create_provider  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="decision_analysis",
        description="Decision analysis pipeline (LLM-assisted when available).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run a canned job-offer example end to end (non-interactive)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the LLM and use the structured input wizard",
    )
    args = parser.parse_args()
    if args.demo:
        run_demo()
        return
    if args.no_llm:
        run_interactive()
        return
    provider = create_provider()
    if provider.available:
        run_llm(provider)
    else:
        print(
            "LLM mode unavailable (set ANTHROPIC_API_KEY in .env or the "
            "environment, and pip install claude-agent-sdk)."
        )
        print("Falling back to the structured wizard.\n")
        run_interactive()


if __name__ == "__main__":
    main()
