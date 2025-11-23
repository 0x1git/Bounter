"""CLI entry point for the Bounter agent."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import box
from rich.text import Text

from bounter.agent import BounterAgent
from bounter.cli import parse_args
from bounter.config import BounterConfig
from bounter.reporting import ScanReport


def _print_report(console: Console, report: ScanReport) -> None:
    """Echo the thinking summary, final analysis, and token usage."""

    console.rule("[bold green]Final Analysis")
    if report.final_analysis:
        console.print(
            Panel(
                Markdown(report.final_analysis.strip()),
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        console.print(Panel(Text("Model did not return a final analysis.", style="dim"), border_style="green"))

    if report.total_tokens is not None:
        console.rule("[bold magenta]Token Usage")
        table = Table(box=box.SIMPLE_HEAVY)
        table.add_column("Metric", style="bold magenta")
        table.add_column("Tokens", justify="right")
        table.add_row("Thinking", str(report.thinking_tokens or 0))
        table.add_row("Output", str(report.output_tokens or 0))
        table.add_row("Total", str(report.total_tokens))
        console.print(Panel(table, border_style="magenta", padding=(1, 2)))


def _persist_report(console: Console, report: ScanReport, report_dir: Path, prefix: str) -> None:
    """Write JSON and Markdown snapshots to disk."""

    timestamp = (report.end_time or datetime.utcnow()).strftime("%Y%m%d-%H%M%S")
    base = report_dir / f"{prefix}-{timestamp}"
    report.save_json(base.with_suffix(".json"))
    report.save_markdown(base.with_suffix(".md"))
    console.print(
        Panel(
            Text(
                f"Reports saved to {base.with_suffix('.json')} and {base.with_suffix('.md')}",
                style="bold green",
            ),
            title="Artifacts",
            border_style="green",
        )
    )


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

    console.rule("[bold white]Autonomous Bug Bounty Agent")

    with progress:
        agent = BounterAgent(
            config=config,
            report=report,
            verbose=args.verbose,
            status_console=console,
            progress=progress,
        )
        response = agent.run(target=args.target, description=args.description)
    _print_report(console, report)
    _persist_report(console, report, args.report_dir, args.report_prefix)


if __name__ == "__main__":
    main()