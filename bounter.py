"""CLI entry point for the Bounter agent."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from bounter.agent import BounterAgent
from bounter.cli import parse_args
from bounter.config import BounterConfig
from bounter.reporting import ScanReport


def _print_report(report: ScanReport) -> None:
    """Echo the thinking summary, final analysis, and token usage."""

    print("\n🧠 THINKING SUMMARY:")
    print("-" * 50)
    if report.thinking_summary:
        for thought in report.thinking_summary:
            print(thought)
            print("-" * 50)
    else:
        print("No thinking output captured.")

    print("\n FINAL ANALYSIS:")
    print("-" * 50)
    if report.final_analysis:
        print(report.final_analysis)
    else:
        print("Model did not return a final analysis.")

    if report.total_tokens is not None:
        print("\n TOKEN USAGE:")
        print(f"Thinking tokens: {report.thinking_tokens}")
        print(f"Output tokens: {report.output_tokens}")
        print(f"Total tokens: {report.total_tokens}")


def _print_model_response(response) -> None:
    """Display the raw response content returned by Gemini."""

    print("\n📨 MODEL RESPONSE:")
    print("-" * 50)
    try:
        candidates = getattr(response, "candidates", []) or []
        if not candidates:
            print("No candidates returned by the model.")
            return
        for idx, candidate in enumerate(candidates, start=1):
            print(f"Candidate {idx}:")
            parts = getattr(candidate, "content", None)
            for part in getattr(parts, "parts", []) or []:
                text = getattr(part, "text", "")
                if not text:
                    continue
                label = "THOUGHT" if getattr(part, "thought", False) else "OUTPUT"
                print(f"[{label}] {text}")
            print("-" * 50)
    except Exception as exc:  # pragma: no cover - defensive log only
        print(f"Unable to display model response: {exc}")


def _persist_report(report: ScanReport, report_dir: Path, prefix: str) -> None:
    """Write JSON and Markdown snapshots to disk."""

    timestamp = (report.end_time or datetime.utcnow()).strftime("%Y%m%d-%H%M%S")
    base = report_dir / f"{prefix}-{timestamp}"
    report.save_json(base.with_suffix(".json"))
    report.save_markdown(base.with_suffix(".md"))
    print(f"\n📝 Reports saved to {base.with_suffix('.json')} and {base.with_suffix('.md')}")


def main() -> None:
    """Entrypoint executed via `python bounter.py`."""

    args = parse_args()
    config = BounterConfig.from_env()
    report = ScanReport(target=args.target, description=args.description)
    console = Console()
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )

    print("\nAutonomous Bug Bounty Agent - Real-time Execution:")
    print("=" * 60)

    with progress:
        agent = BounterAgent(
            config=config,
            report=report,
            verbose=args.verbose,
            status_console=console,
            progress=progress,
        )
        response = agent.run(target=args.target, description=args.description)
    _print_model_response(response)
    _print_report(report)
    _persist_report(report, args.report_dir, args.report_prefix)


if __name__ == "__main__":
    main()