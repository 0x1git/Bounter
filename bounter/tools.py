"""Tool factory functions used by the Gemini agent."""

import json
import shlex
import shutil
import subprocess
import uuid
from contextlib import nullcontext
from pathlib import Path
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
        output_console.print(Text(f"╰┈➤ {command}", style="bold white"))
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


def build_searchsploit_tool(
    report: "ScanReport",
    *,
    verbose: bool = False,
    on_command: Optional[Callable[[dict[str, Any]], None]] = None,
    status_console: Optional[Console] = None,
    progress: Optional[Progress] = None,
    default_download_dir: Optional[str] = None,
) -> Callable[..., dict[str, Any]]:
    """Return a callable that surfaces searchsploit lookups and mirroring."""

    tool_name = "searchsploit_lookup"
    output_console = status_console or Console()
    downloads_root = Path(default_download_dir or "searchsploit-downloads").expanduser()

    def _format_command(command: list[str]) -> str:
        return " ".join(shlex.quote(part) for part in command)

    def _emit(label: str, payload: str, *, border: str = "blue") -> None:
        if not payload:
            return
        _render_stream(output_console, label, payload, border=border)

    def _log_event(data: dict[str, Any]) -> None:
        report.log_command(data)
        if on_command:
            on_command(data)

    def _run_subprocess(command: list[str], label: str) -> dict[str, Any]:
        command_str = _format_command(command)
        event_id = uuid.uuid4().hex
        if verbose:
            output_console.print(Text(f"Executing {command_str}", style="dim"))
        if on_command:
            on_command(
                {
                    "tool_name": tool_name,
                    "command": command_str,
                    "command_executed": command_str,
                    "phase": "start",
                    "event_id": event_id,
                }
            )

        status_cm = (
            status_console.status(f"[cyan]{tool_name} → {label}", spinner="dots8")
            if status_console
            else nullcontext()
        )
        progress_cm = track_progress(progress, f"{tool_name} → {label}")

        with status_cm, progress_cm:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        success = result.returncode == 0
        payload = {
            "tool_name": tool_name,
            "command_executed": command_str,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "return_code": result.returncode,
            "success": success,
            "phase": "end",
            "event_id": event_id,
        }
        _log_event(payload)

        header = f"{label.upper()} {'OK' if success else 'FAILED'}"
        border = "green" if success else "red"
        _emit(header, payload["stdout"], border=border)
        if payload["stderr"]:
            _emit(f"{label.upper()} STDERR", payload["stderr"], border="yellow")

        return payload

    def _extract_path(stdout: str) -> Optional[str]:
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower().startswith("path:"):
                _, _, remainder = stripped.partition(":")
                candidate = remainder.strip()
                if candidate:
                    return candidate
            if stripped.startswith("/"):
                return stripped
        return None

    def _ensure_download_dir(path_hint: Optional[str]) -> Path:
        target = Path(path_hint).expanduser() if path_hint else downloads_root
        target.mkdir(parents=True, exist_ok=True)
        return target

    def searchsploit_lookup(
        action: str = "search",
        query: Optional[str] = None,
        cve_id: Optional[str] = None,
        edb_id: Optional[str] = None,
        max_results: int = 20,
        mirror_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
    ) -> dict[str, Any]:
        action_normalized = (action or "search").strip().lower()
        output_console.print(Text(f"\ntool → {tool_name}", style="bold cyan"))
        output_console.print(Text(f"action: {action_normalized}", style="bold white"))

        if action_normalized == "search":
            if not query and not cve_id:
                raise ValueError("Either 'query' or 'cve_id' is required for search")
            command = ["searchsploit", "--json"]
            if cve_id:
                command.extend(["--cve", cve_id])
            if query:
                command.append(query)
            result = _run_subprocess(command, label="search")
            if not result["success"]:
                return {
                    "success": False,
                    "error": "searchsploit search failed",
                    "details": result,
                }
            try:
                payload = json.loads(result["stdout"] or "{}")
            except json.JSONDecodeError as exc:
                return {
                    "success": False,
                    "error": f"Unable to parse searchsploit JSON output: {exc}",
                }

            entries: list[dict[str, Any]] = []
            combined = (
                payload.get("RESULTS_EXPLOIT", [])
                + payload.get("RESULTS_SHELLCODE", [])
            )
            limit = max(1, min(max_results or 20, 100))
            for row in combined[:limit]:
                entries.append(
                    {
                        "edb_id": row.get("EDB-ID"),
                        "title": row.get("Title"),
                        "author": row.get("Author"),
                        "type": row.get("Type"),
                        "platform": row.get("Platform"),
                        "date": row.get("Date"),
                        "verified": row.get("Verified"),
                        "path": row.get("Path"),
                    }
                )

            return {
                "success": True,
                "action": "search",
                "total_results": len(combined),
                "returned_results": len(entries),
                "results": entries,
            }

        if action_normalized == "mirror":
            if not edb_id:
                raise ValueError("'edb_id' is required when action='mirror'")
            path_result = _run_subprocess(["searchsploit", "-p", edb_id], label="path")
            if not path_result["success"]:
                return {
                    "success": False,
                    "error": "Unable to resolve exploit path",
                    "details": path_result,
                }
            source_path_str = _extract_path(path_result["stdout"])
            if not source_path_str:
                return {
                    "success": False,
                    "error": "searchsploit did not return a usable path",
                    "details": path_result,
                }
            source_path = Path(source_path_str)
            if not source_path.exists():
                return {
                    "success": False,
                    "error": f"Exploit file not found: {source_path}",
                }

            destination_dir = _ensure_download_dir(mirror_directory)
            destination_path = destination_dir / source_path.name
            shutil.copy2(source_path, destination_path)
            output_console.print(
                Text(
                    f"mirrored exploit → {destination_path}",
                    style="bold green",
                )
            )

            response: dict[str, Any] = {
                "success": True,
                "action": "mirror",
                "edb_id": edb_id,
                "source_path": str(source_path),
                "copied_to": str(destination_path),
            }

            if execute_command:
                exec_payload = _run_subprocess(
                    ["/bin/sh", "-c", execute_command],
                    label="exploit-test",
                )
                response["execution"] = exec_payload

            return response

        raise ValueError(
            "Unsupported action. Use 'search' or 'mirror' for the searchsploit tool."
        )

    return searchsploit_lookup
