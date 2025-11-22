"""Tool factory functions used by the Gemini agent."""

import json
import subprocess
import uuid
from contextlib import nullcontext
from typing import Any, Callable, TYPE_CHECKING, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.syntax import Syntax
from rich.text import Text

if TYPE_CHECKING:  # pragma: no cover - runtime import avoided
    from .reporting import ScanReport

from .progress_utils import track_progress


def _format_stream_content(content: str) -> Text | Syntax:
    stripped = content.strip()
    if not stripped:
        return Text("<empty>", style="dim")

    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            pretty = json.dumps(parsed, indent=2, sort_keys=True)
            return Syntax(pretty, "json", word_wrap=True)

    if stripped.startswith("GET ") or " HTTP/" in stripped:
        return Syntax(stripped, "http", word_wrap=True)

    if "\n" in stripped or any(token in stripped for token in (" && ", " || ", " | ", "#!/bin")):
        return Syntax(stripped, "bash", word_wrap=True)

    return Text(stripped)


def _render_stream(console: Console, label: str, content: str, *, border: str) -> None:
    if content is None:
        return
    renderable = _format_stream_content(content)
    console.print(
        Panel(
            renderable,
            title=label,
            border_style=border,
            padding=(1, 2),
        )
    )


def build_system_command_tool(
    report: "ScanReport",
    timeout: int = 30,
    verbose: bool = False,
    on_command: Optional[Callable[[dict[str, Any]], None]] = None,
    status_console: Optional[Console] = None,
    progress: Optional[Progress] = None,
) -> Callable[[str], dict[str, Any]]:
    """Return a callable that executes system commands and logs results."""

    tool_name = "build_system_command_tool"

    def execute_system_command_impl(command: str) -> dict[str, Any]:
        # Show real-time execution feedback
        output_console = status_console or Console()
        output_console.print(
            Text(f"\ntool → {tool_name}", style="bold cyan\n")
        )
        output_console.print(Text(f"❯ {command}", style="bold white"))
        event_id = uuid.uuid4().hex

        if on_command:
            on_command(
                {
                    "tool_name": tool_name,
                    "command": command,
                    "command_executed": command,
                    "phase": "start",
                    "event_id": event_id,
                }
            )

        status_cm = (
            status_console.status(f"[cyan]tool → {command}", spinner="dots8")
            if status_console
            else nullcontext()
        )

        progress_cm = track_progress(progress, f"tool → {command}")

        with progress_cm, status_cm:
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout,
                )

                stdout = result.stdout.strip()
                stderr = result.stderr.strip()
                _render_stream(output_console, "STDOUT", stdout, border="green")
                output_console.print(Text(f"Return Code: {result.returncode}", style="bold white"))

                payload = {
                    "stdout": stdout,
                    "stderr": stderr,
                    "command_executed": command,
                    "return_code": result.returncode,
                    "success": True,
                    "tool_name": tool_name,
                    "phase": "end",
                    "event_id": event_id,
                }
                report.log_command(payload)
                if on_command:
                    on_command(payload)
                return payload
            except subprocess.CalledProcessError as exc:
                stdout = exc.stdout.strip() if exc.stdout else ""
                stderr = exc.stderr.strip() if exc.stderr else ""
                output_console.print(Text("❌ Command failed", style="bold red"))
                output_console.print(Text(f"Return Code: {exc.returncode}", style="bold red"))
                if stdout:
                    _render_stream(output_console, "STDOUT", stdout, border="green")
                if stderr:
                    _render_stream(output_console, "STDERR", stderr, border="red")
                payload = {
                    "stdout": stdout,
                    "stderr": stderr,
                    "command_executed": command,
                    "return_code": exc.returncode,
                    "error": str(exc),
                    "success": False,
                    "tool_name": tool_name,
                    "phase": "end",
                    "event_id": event_id,
                }
                report.log_command(payload)
                if on_command:
                    on_command(payload)
                return payload
            except subprocess.TimeoutExpired:
                output_console.print(Text(f"⏱️ Command timed out after {timeout} seconds", style="yellow"))
                payload = {
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout} seconds",
                    "command_executed": command,
                    "error": "Timeout",
                    "success": False,
                    "tool_name": tool_name,
                    "phase": "end",
                    "event_id": event_id,
                }
                report.log_command(payload)
                if on_command:
                    on_command(payload)
                return payload

    return execute_system_command_impl
