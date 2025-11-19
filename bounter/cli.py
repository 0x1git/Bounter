"""Command-line interface helpers for Bounter."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser used by the CLI entry point."""

    parser = argparse.ArgumentParser(
        prog="bounter",
        description="Autonomous bug bounty hunter powered by Gemini",
    )
    parser.add_argument(
        "target",
        help="Root URL or asset identifier to test (e.g. http://localhost:8080)",
    )
    parser.add_argument(
        "--description",
        "-d",
        help="Short description of the expected vulnerability or scope",
        default="",
    )
    parser.add_argument(
        "--report-dir",
        help="Directory where reports will be written",
        type=Path,
        default=Path("reports"),
    )
    parser.add_argument(
        "--report-prefix",
        help="Optional filename prefix for saved reports",
        default="scan",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help="Reserved for future multi-iteration workflows",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable additional console output",
    )
    return parser


def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI args, optionally using a provided list (for tests)."""

    parser = build_parser()
    return parser.parse_args(args=args)
