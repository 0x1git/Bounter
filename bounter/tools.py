"""Tool factory functions used by the Gemini agent."""

import io
import json
import shlex
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import traceback
import uuid
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from datetime import datetime
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
                # STDERR suppressed per user preference
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


def build_listener_tool(
    report: "ScanReport",
    *,
    verbose: bool = False,
    on_command: Optional[Callable[[dict[str, Any]], None]] = None,
    status_console: Optional[Console] = None,
    progress: Optional[Progress] = None,
) -> Callable[..., dict[str, Any]]:
    """Return a callable that manages background nc listeners."""

    tool_name = "start_listener"
    output_console = status_console or Console()
    listeners: dict[str, dict[str, Any]] = {}

    def _command_label(action: str, port_label: str) -> str:
        return f"{tool_name} action={action} port={port_label}"

    def _log(payload: dict[str, Any]) -> None:
        report.log_command(payload)
        if on_command:
            on_command(payload)

    def _emit(label: str, data: str, *, border: str) -> None:
        if not data:
            return
        _render_stream(output_console, label, data, border=border)

    def _drain_buffer(session: dict[str, Any], key: str, *, drain: bool) -> str:
        lock: threading.Lock = session["lock"]
        buffer: list[str] = session[key]
        with lock:
            content = "".join(buffer)
            if drain:
                buffer.clear()
        return content

    def _reader(stream, buffer: list[str], lock: threading.Lock) -> None:
        try:
            for chunk in iter(stream.readline, ""):
                if not chunk:
                    break
                with lock:
                    buffer.append(chunk)
        finally:
            stream.close()

    def _normalize_port(port: int | str | None) -> str:
        if port is None:
            raise ValueError("'port' is required for listener actions")
        try:
            port_int = int(port)
        except (TypeError, ValueError) as exc:  # pragma: no cover - validation
            raise ValueError("'port' must be an integer") from exc
        if port_int <= 0:
            raise ValueError("'port' must be positive")
        return str(port_int)

    def start_listener(
        action: str = "start",
        port: int = 0,
        input_data: Optional[str] = None,
        drain_output: bool = True,
        bind_address: str = "0.0.0.0",
    ) -> dict[str, Any]:
        action_normalized = (action or "start").strip().lower()
        if action_normalized == "drain_output" or action_normalized == "drain":
            action_normalized = "read"
        port_key = _normalize_port(port)
        output_console.print(Text(f"\ntool → {tool_name}", style="bold cyan"))
        output_console.print(Text(f"action: {action_normalized} port={port_key}", style="bold white"))

        session = listeners.get(port_key)

        if action_normalized == "start":
            if session and session.get("process") and session["process"].poll() is None:
                return {
                    "success": False,
                    "error": f"listener already running on port {port_key}",
                    "port": port_key,
                }

            command = [
                "nc",
                "-lnvp",
                port_key,
            ]
            if bind_address and bind_address not in {"0.0.0.0", ""}:
                command.extend(["-s", bind_address])

            event_id = uuid.uuid4().hex
            command_label = _command_label("start", port_key)
            if on_command:
                on_command(
                    {
                        "tool_name": tool_name,
                        "command": " ".join(command),
                        "command_executed": command_label,
                        "phase": "start",
                        "event_id": event_id,
                    }
                )

            progress_cm = track_progress(progress, f"{tool_name} → start {port_key}")
            status_cm = (
                status_console.status(f"[cyan]{tool_name} listening on {port_key}", spinner="dots8")
                if status_console
                else nullcontext()
            )

            try:
                with progress_cm, status_cm:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                    )
            except FileNotFoundError as exc:
                payload = {
                    "tool_name": tool_name,
                    "command_executed": " ".join(command),
                    "stdout": "",
                    "stderr": str(exc),
                    "return_code": 1,
                    "success": False,
                    "event_id": event_id,
                    "error": "nc binary not found",
                }
                _log(payload)
                return payload

            lock = threading.Lock()
            stdout_buf: list[str] = []
            stderr_buf: list[str] = []
            stdout_thread = threading.Thread(
                target=_reader,
                args=(process.stdout, stdout_buf, lock),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_reader,
                args=(process.stderr, stderr_buf, lock),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            session = {
                "process": process,
                "stdout": stdout_buf,
                "stderr": stderr_buf,
                "lock": lock,
                "started_at": datetime.utcnow().isoformat(),
                "stdout_thread": stdout_thread,
                "stderr_thread": stderr_thread,
            }
            listeners[port_key] = session

            payload = {
                "tool_name": tool_name,
                "command_executed": command_label,
                "stdout": "",
                "stderr": "",
                "return_code": 0,
                "success": True,
                "event_id": event_id,
                "port": port_key,
                "action": "start",
            }
            _log(payload)
            return {
                "success": True,
                "message": f"listener started on port {port_key}",
                "port": port_key,
                "pid": process.pid,
                "started_at": session["started_at"],
            }

        if action_normalized not in {"status", "send", "read", "stop"}:
            raise ValueError("Unsupported action. Use start, status, send, read, or stop.")

        if not session:
            raise ValueError(f"No listener tracked for port {port_key}")

        process: subprocess.Popen = session["process"]
        command_label = _command_label(action_normalized, port_key)

        if action_normalized == "status":
            running = process.poll() is None
            payload = {
                "success": True,
                "action": "status",
                "port": port_key,
                "running": running,
                "return_code": process.returncode,
                "started_at": session.get("started_at"),
            }
            _log({
                "tool_name": tool_name,
                "command_executed": command_label,
                "stdout": "",
                "stderr": "",
                "return_code": process.returncode,
                "success": True,
                "details": payload,
            })
            return payload

        if action_normalized == "send":
            if process.poll() is not None:
                raise RuntimeError("Listener process is not running")
            if not input_data:
                raise ValueError("'input_data' is required when action='send'")
            assert process.stdin is not None  # nosec - ensured by Popen config
            process.stdin.write(input_data)
            if not input_data.endswith("\n"):
                process.stdin.write("\n")
            process.stdin.flush()
            payload = {
                "success": True,
                "action": "send",
                "port": port_key,
                "bytes_sent": len(input_data),
            }
            _log(
                {
                    "tool_name": tool_name,
                    "command_executed": command_label,
                    "stdout": "",
                    "stderr": "",
                    "return_code": 0,
                    "success": True,
                    "details": payload,
                }
            )
            return payload

        if action_normalized == "read":
            stdout_text = _drain_buffer(session, "stdout", drain=drain_output)
            stderr_text = _drain_buffer(session, "stderr", drain=drain_output)
            _emit("LISTENER STDOUT", stdout_text, border="green")
            payload = {
                "success": True,
                "action": "read",
                "port": port_key,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "drained": drain_output,
            }
            _log(
                {
                    "tool_name": tool_name,
                    "command_executed": command_label,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "return_code": 0,
                    "success": True,
                }
            )
            return payload

        # stop
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        listeners.pop(port_key, None)
        stdout = _drain_buffer(session, "stdout", drain=True)
        stderr = _drain_buffer(session, "stderr", drain=True)
        _emit("LISTENER STDOUT", stdout, border="green")
        command_label = _command_label("stop", port_key)
        payload = {
            "success": True,
            "action": "stop",
            "port": port_key,
            "return_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "tool_name": tool_name,
            "command_executed": command_label,
        }
        _log(payload)
        return payload

    return start_listener


def build_python_executor_tool(
    report: "ScanReport",
    *,
    verbose: bool = False,
    on_command: Optional[Callable[[dict[str, Any]], None]] = None,
    status_console: Optional[Console] = None,
    progress: Optional[Progress] = None,
) -> Callable[..., dict[str, Any]]:
    """Return a callable that executes Python snippets with session memory."""

    tool_name = "python_code_executor"
    output_console = status_console or Console()
    sessions: dict[str, dict[str, Any]] = {}

    sigalrm = getattr(signal, "SIGALRM", None)
    alarm_fn = getattr(signal, "alarm", None)

    def _ensure_session(session_id: str, reset: bool) -> dict[str, Any]:
        if reset or session_id not in sessions:
            sessions[session_id] = {
                "globals": {"__builtins__": __builtins__},
                "history": [],
            }
        return sessions[session_id]

    def _install_requirements(packages: list[str] | None) -> list[dict[str, Any]]:
        installs: list[dict[str, Any]] = []
        if not packages:
            return installs
        for package in packages:
            cmd = [sys.executable, "-m", "pip", "install", package]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            installs.append(
                {
                    "package": package,
                    "return_code": proc.returncode,
                    "stdout": proc.stdout.strip(),
                    "stderr": proc.stderr.strip(),
                }
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"pip install failed for '{package}': {proc.stderr.strip()}"
                )
        return installs

    def _render_code_snippet(snippet: str) -> None:
        output_console.print(
            Panel(
                Syntax(snippet, "python", word_wrap=True),
                title="python code",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    def _log_payload(payload: dict[str, Any]) -> None:
        report.log_command(payload)
        if on_command:
            on_command(payload)

    def python_code_executor(
        code: str,
        *,
        session_id: Optional[str] = None,
        reset_session: bool = False,
        requirements: Optional[list[str]] = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("'code' must be a non-empty string")

        sid = session_id or "default"
        session = _ensure_session(sid, reset_session)

        output_console.print(Text(f"\ntool → {tool_name}", style="bold cyan"))
        output_console.print(Text(f"session: {sid}", style="bold white"))

        installs: list[dict[str, Any]] = []
        if requirements:
            try:
                installs = _install_requirements(requirements)
            except RuntimeError as exc:
                error_payload = {
                    "tool_name": tool_name,
                    "command_executed": f"pip install {requirements}",
                    "stdout": "",
                    "stderr": str(exc),
                    "return_code": 1,
                    "success": False,
                    "session_id": sid,
                    "installed": installs,
                    "error": str(exc),
                }
                _log_payload(error_payload)
                return error_payload

        normalized_code = textwrap.dedent(code).rstrip()
        if not normalized_code:
            raise ValueError("Provided code is empty after normalization")

        _render_code_snippet(normalized_code)

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        globals_ns = session["globals"]
        history = session["history"]
        error_trace = None
        eval_result = None

        def _timeout_handler(signum, frame):  # pragma: no cover - signal safety
            raise TimeoutError("Code execution timed out")

        if sigalrm is not None and alarm_fn is not None:
            previous_handler = signal.signal(sigalrm, _timeout_handler)
            alarm_fn(max(1, timeout))
        else:
            previous_handler = None
        try:
            try:
                compiled = compile(normalized_code, "<agent-python>", "eval")
                is_expr = True
            except SyntaxError:
                compiled = compile(normalized_code, "<agent-python>", "exec")
                is_expr = False

            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                if is_expr:
                    eval_result = eval(compiled, globals_ns, globals_ns)
                    globals_ns["_"] = eval_result
                else:
                    exec(compiled, globals_ns, globals_ns)
                    eval_result = globals_ns.get("_")
        except Exception as exc:  # pragma: no cover - runtime safety
            error_trace = "".join(
                traceback.format_exception(exc.__class__, exc, exc.__traceback__)
            )
        finally:
            if sigalrm is not None and previous_handler is not None and alarm_fn is not None:
                alarm_fn(0)
                signal.signal(sigalrm, previous_handler)

        stdout_text = stdout_buffer.getvalue().strip()
        stderr_text = stderr_buffer.getvalue().strip()

        history.append(
            {
                "code": normalized_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "error": error_trace,
                "result_preview": repr(eval_result)[:500],
            }
        )

        variables = sorted(
            name
            for name in globals_ns.keys()
            if not name.startswith("__") and not name.startswith("_pip_")
        )

        payload = {
            "tool_name": tool_name,
            "command_executed": normalized_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "return_code": 0 if error_trace is None else 1,
            "success": error_trace is None,
            "session_id": sid,
            "installed": installs,
            "variables": variables,
            "result_repr": repr(eval_result) if eval_result is not None else None,
            "history_length": len(history),
            "error": error_trace,
        }
        _log_payload(payload)

        _render_stream(output_console, "STDOUT", stdout_text, border="green")
        if error_trace:
            _render_stream(output_console, "TRACEBACK", error_trace, border="red")

        return payload

    return python_code_executor


