"""Core Gemini agent orchestration."""
from __future__ import annotations

from contextlib import nullcontext
import time
from typing import Any, Callable, Optional

from google import genai
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress
from rich.status import Status

from .config import BounterConfig
from .reporting import CommandRecord, ScanReport
from .tools import (
    build_listener_tool,
    build_python_executor_tool,
    build_searchsploit_tool,
    build_system_command_tool,
)
from .progress_utils import track_progress


class BounterAgent:
    """Coordinates Gemini interactions, tools, and reporting."""

    RATE_LIMIT_BUFFER = 3  # stop using a model this many requests before its RPM
    RATE_LIMIT_WINDOW = 60  # seconds

    def __init__(
        self,
        config: BounterConfig,
        report: ScanReport,
        client: Optional[genai.Client] = None,
        verbose: bool = False,
        on_tool_event: Optional[Callable[[dict[str, Any]], None]] = None,
        status_console: Optional[Console] = None,
        progress: Optional[Progress] = None,
    ) -> None:
        self.config = config
        self.report = report
        self.client = client or genai.Client()
        self.verbose = verbose
        self.on_tool_event = on_tool_event
        self.status_console = status_console
        self.progress = progress
        self._rate_limit_notes: list[str] = []
        self._incomplete_response_notes: list[str] = []
        self._stream_started: dict[str, bool] = {}
        self._thinking_status: Optional[Status] = None
        self._thinking_started_at: Optional[float] = None

    RATE_LIMIT_KEYWORDS = (
        "rate limit",
        "rate-limit",
        "quota",
        "too many requests",
        "429",
        "limit reached",
    )

    INCOMPLETE_RESPONSE_RETRIES = 2

    ITERATION_KEYWORDS = (
        " for ",
        "while ",
        "| while",
        " seq ",
        "xargs",
        "parallel ",
        "ffuf",
        "wfuzz",
        "hydra",
        "gobuster",
        "dirsearch",
        "dirb",
        "intruder",
        "nuclei",
        "patator",
    )

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[agent] {message}")

    def _response_indicates_rate_limit(self, response: genai.types.GenerateContentResponse) -> bool:
        """Best-effort detection when the model reports a rate-limit in text."""

        try:
            candidates = getattr(response, "candidates", []) or []
        except AttributeError:
            return False

        for candidate in candidates:
            parts = getattr(candidate, "content", None)
            for part in getattr(parts, "parts", []) or []:
                text = getattr(part, "text", "")
                if not text:
                    continue
                lower = text.lower()
                if any(keyword in lower for keyword in self.RATE_LIMIT_KEYWORDS):
                    return True
        return False

    def _record_rate_limit_note(self, model_name: str, detail: str | None = None) -> None:
        note = f"Model '{model_name}' hit a rate/availability limit"
        if detail:
            note += f": {detail}"
        if note not in self._rate_limit_notes:
            self._rate_limit_notes.append(note)

    def _record_incomplete_response_note(self, note: str) -> None:
        if note not in self._incomplete_response_notes:
            self._incomplete_response_notes.append(note)
            max_notes = 5
            if len(self._incomplete_response_notes) > max_notes:
                self._incomplete_response_notes = self._incomplete_response_notes[-max_notes:]

    def _handle_incomplete_response(self, model_name: str, attempt: int) -> None:
        message = (
            f"Model '{model_name}' returned no final analysis (attempt {attempt}); prompting it to continue."
        )
        self._log(message)
        self._record_incomplete_response_note(message)

    def _is_rate_limit_error(self, code: Any, message: str) -> bool:
        if code in {429, 503}:
            return True
        keywords = [
            "rate limit",
            "quota",
            "too many requests",
            "overloaded",
            "unavailable",
            "exhausted",
        ]
        text = (message or "").lower()
        return any(keyword in text for keyword in keywords)

    def _python_executor_usage_lines(self) -> list[str]:
        commands = self.report.commands
        if not commands:
            return []

        python_uses = sum(
            1 for record in commands if getattr(record, "tool_name", None) == "python_code_executor"
        )
        total = len(commands)
        ratio = python_uses / total if total else 0
        lines = [
            f"python_code_executor usage so far: {python_uses}/{total} tool calls."
        ]

        if python_uses == 0:
            lines.append(
                "Reminder: port fuzzing/bruteforce/dictionary iterations into python_code_executor before launching shell loops."
            )
        elif ratio < 0.25 and total >= 4:
            lines.append(
                "You can accelerate remaining payload work by shifting it into python_code_executor sessions."
            )

        if self._recent_iterative_shell_usage(commands) and python_uses == 0:
            lines.append(
                "Detected shell-based iteration recently; reimplement those loops inside python_code_executor."
            )

        return lines

    def _listener_context_lines(self) -> list[str]:
        commands = self.report.commands
        if not commands:
            return []

        listeners: dict[str, dict[str, Any]] = {}
        for record in commands:
            if getattr(record, "tool_name", None) != "start_listener":
                continue
            action, port = self._parse_listener_command(record.command)
            if not port:
                continue
            info = listeners.setdefault(
                port,
                {
                    "running": False,
                    "last_action": None,
                    "had_read": False,
                    "last_output": None,
                },
            )
            info["last_action"] = action
            if action == "start" and record.success:
                info["running"] = True
                info["had_read"] = False
                info["last_output"] = None
            elif action == "read":
                info["had_read"] = True
                info["last_output"] = record.stdout or record.stderr
            elif action == "stop":
                info["running"] = False

        lines: list[str] = []
        for port, info in listeners.items():
            running = info.get("running", False)
            had_read = info.get("had_read", False)
            last_action = info.get("last_action")
            last_output = info.get("last_output")
            status = "RUNNING" if running else "STOPPED"
            snippet = (
                last_output[:80].replace("\n", " ") + "…"
                if last_output and len(last_output) > 80
                else last_output
            )
            lines.append(
                f"listener port {port}: status={status}, last_action={last_action}, had_read={had_read}"
            )
            if running and not had_read:
                lines.append(
                    f"listener port {port} has not produced output yet—poll start_listener(action='read') and craft new payloads if still quiet."
                )
            if snippet:
                lines.append(f"last output preview: {snippet}")
        return lines

    def _parse_listener_command(self, command_text: str | None) -> tuple[Optional[str], Optional[str]]:
        if not command_text or "start_listener" not in command_text:
            return None, None
        action = None
        port = None
        for token in command_text.split():
            if token.startswith("action="):
                action = token.partition("=")[2]
            elif token.startswith("port="):
                port = token.partition("=")[2]
        return action, port

    def _recent_iterative_shell_usage(
        self, commands: list[CommandRecord], lookback: int = 5
    ) -> bool:
        subset = commands[-lookback:]
        for record in subset:
            if self._looks_iterative_shell(record.command):
                return True
        return False

    def _looks_iterative_shell(self, command_text: str | None) -> bool:
        if not command_text:
            return False
        lowered = command_text.lower()
        if lowered.startswith("for ") or lowered.startswith("while "):
            return True
        return any(keyword in lowered for keyword in self.ITERATION_KEYWORDS)

    def build_prompt(self, target: str, description: str) -> str:
        """Compose the user prompt delivered to the Gemini model."""

        base = f"Test the web app at {target}."
        if description:
            base += f" DESCRIPTION: {description.strip()}"
        self._log(f"Prompt prepared: {base}")
        return base

    def run(self, target: str, description: str) -> genai.types.GenerateContentResponse:
        """Execute the scan and return the Gemini response."""
        self._log("Building tools and configuration")

        tried_models: list[str] = []
        last_exception: Optional[Exception] = None
        self._rate_limit_notes = []

        for model_name in self.config.models_order:
            tried_models.append(model_name)
            attempt = 0

            while True:
                attempt += 1
                prompt = self.build_prompt(target, description)

                if (
                    len(tried_models) > 1
                    or self._rate_limit_notes
                    or self._incomplete_response_notes
                ):
                    context_lines = []
                    if len(tried_models) > 1:
                        context_lines.append(
                            "Previously attempted models: " + ", ".join(tried_models[:-1])
                        )
                    if self.report.commands:
                        context_lines.append("Commands executed so far:")
                        for cmd in self.report.commands:
                            context_lines.append(f"- {cmd.command} (success={cmd.success})")
                        python_usage_lines = self._python_executor_usage_lines()
                        if python_usage_lines:
                            context_lines.append("python_code_executor reminders:")
                            context_lines.extend(f"- {line}" for line in python_usage_lines)
                        listener_lines = self._listener_context_lines()
                        if listener_lines:
                            context_lines.append("start_listener observations:")
                            context_lines.extend(f"- {line}" for line in listener_lines)
                    if self.report.thinking_summary:
                        context_lines.append("Thinking summary so far:")
                        context_lines.extend(f"- {t}" for t in self.report.thinking_summary)
                    if self.report.final_analysis:
                        context_lines.append("Final analysis so far:")
                        context_lines.append(self.report.final_analysis)
                    if self._rate_limit_notes:
                        context_lines.append("Rate limit observations:")
                        context_lines.extend(f"- {note}" for note in self._rate_limit_notes)
                    if self._incomplete_response_notes:
                        context_lines.append("Incomplete response observations:")
                        context_lines.extend(
                            f"- {note}" for note in self._incomplete_response_notes
                        )

                    if context_lines:
                        prompt = prompt + "\n\nCONTEXT:\n" + "\n".join(context_lines)

                system_tool = build_system_command_tool(
                    report=self.report,
                    timeout=self.config.command_timeout,
                    verbose=self.verbose,
                    on_command=self.on_tool_event,
                    status_console=self.status_console,
                    progress=self.progress,
                )
                search_tool = build_searchsploit_tool(
                    report=self.report,
                    verbose=self.verbose,
                    on_command=self.on_tool_event,
                    status_console=self.status_console,
                    progress=self.progress,
                )
                python_tool = build_python_executor_tool(
                    report=self.report,
                    verbose=self.verbose,
                    on_command=self.on_tool_event,
                    status_console=self.status_console,
                    progress=self.progress,
                )
                listener_tool = build_listener_tool(
                    report=self.report,
                    verbose=self.verbose,
                    on_command=self.on_tool_event,
                    status_console=self.status_console,
                    progress=self.progress,
                )
                tools = [system_tool, search_tool, python_tool, listener_tool]

                content_config = self.config.build_content_config(tools, model_name=model_name)

                self._log(
                    f"Dispatching prompt to model '{model_name}' (attempt {len(tried_models)}.{attempt})"
                )

                progress_cm = track_progress(self.progress, f"[cyan]thinking → {model_name}")

                try:
                    with progress_cm:
                        response = self._stream_model_response(
                            model_name=model_name,
                            prompt=prompt,
                            content_config=content_config,
                        )
                    self._log(
                        f"Model '{model_name}' responded successfully (attempt {attempt})"
                    )
                    self.report.update_from_response(response)

                    if self._response_indicates_rate_limit(response):
                        self._log(
                            f"Model '{model_name}' reported a rate limit in its response; switching models"
                        )
                        self._record_rate_limit_note(
                            model_name, "Model reported rate limit signal in response"
                        )
                        self.report.end_time = None
                        break

                    if not self.report.final_analysis:
                        self._handle_incomplete_response(model_name, attempt)
                        self.report.end_time = None
                        if attempt < self.INCOMPLETE_RESPONSE_RETRIES:
                            continue
                        exhaustion_note = (
                            f"Model '{model_name}' still produced no final analysis after {attempt} attempts; switching models."
                        )
                        self._record_incomplete_response_note(exhaustion_note)
                        self._log(exhaustion_note)
                        break

                    self._incomplete_response_notes.clear()
                    return response
                except genai.errors.ClientError as exc:  # pragma: no cover - depends on API
                    last_exception = exc
                    code = getattr(exc, "code", None)
                    msg_raw = getattr(exc, "message", None) or str(exc) or ""
                    msg = msg_raw.lower()
                    self._log(
                        f"Model '{model_name}' client error (code={code}, status={getattr(exc, 'status', None)}): {msg}"
                    )

                    if self._is_rate_limit_error(code, msg):
                        self._log(
                            f"Rate/availability limit detected for model '{model_name}' (code={code}); attempting next model"
                        )
                        self._record_rate_limit_note(model_name, msg_raw)
                        break

                    raise
                except Exception as exc:  # pragma: no cover - runtime error handling
                    last_exception = exc
                    msg = str(exc).lower()
                    self._log(f"Model '{model_name}' failed: {msg}")

                    if "no streaming chunks" in msg:
                        self._handle_incomplete_response(model_name, attempt)
                        self.report.end_time = None
                        if attempt < self.INCOMPLETE_RESPONSE_RETRIES:
                            continue
                        exhaustion_note = (
                            f"Model '{model_name}' never produced streaming chunks after {attempt} attempts; switching models."
                        )
                        self._record_incomplete_response_note(exhaustion_note)
                        self._log(exhaustion_note)
                        break

                    if self._is_rate_limit_error(None, msg):
                        self._log(
                            f"Rate limit detected for model '{model_name}', trying next model"
                        )
                        self._record_rate_limit_note(model_name, str(exc))
                        break
                    raise

                # Completed inner loop iteration successfully, break to move on
                break

        if last_exception:
            raise last_exception
        raise RuntimeError("No models available to process the request")

    def _stream_model_response(
        self,
        *,
        model_name: str,
        prompt: str,
        content_config: genai.types.GenerateContentConfig,
    ) -> genai.types.GenerateContentResponse:
        """Stream the model response and return the final chunk."""

        stream = self.client.models.generate_content_stream(
            model=model_name,
            contents=prompt,
            config=content_config,
        )

        final_chunk: Optional[genai.types.GenerateContentResponse] = None
        aggregated_parts: dict[int, list[Any]] = {}
        aggregated_roles: dict[int, Optional[str]] = {}
        candidate_meta: dict[int, genai.types.Candidate] = {}
        usage_metadata = None
        prompt_feedback = None
        response_id = None
        model_version = None
        afc_history = None
        chunk_seen = False
        for chunk in stream:
            chunk_seen = True
            final_chunk = chunk
            usage_metadata = getattr(chunk, "usage_metadata", None) or usage_metadata
            prompt_feedback = getattr(chunk, "prompt_feedback", None) or prompt_feedback
            response_id = getattr(chunk, "response_id", None) or response_id
            model_version = getattr(chunk, "model_version", None) or model_version
            afc_history = (
                getattr(chunk, "automatic_function_calling_history", None)
                or afc_history
            )
            self._handle_stream_chunk(chunk, model_name=model_name)

            candidates = getattr(chunk, "candidates", None) or []
            for idx, candidate in enumerate(candidates):
                candidate_index = getattr(candidate, "index", None)
                if candidate_index is None:
                    candidate_index = idx
                candidate_meta[candidate_index] = candidate
                content = getattr(candidate, "content", None)
                if not content:
                    continue
                aggregated_roles.setdefault(
                    candidate_index, getattr(content, "role", None)
                )
                parts = getattr(content, "parts", None) or []
                if not parts:
                    continue
                bucket = aggregated_parts.setdefault(candidate_index, [])
                bucket.extend(parts)

        if not chunk_seen:
            raise RuntimeError("Model returned no streaming chunks")
        self._finalize_stream_display()
        combined_candidates: list[genai.types.Candidate] = []
        for idx, meta in candidate_meta.items():
            parts = aggregated_parts.get(idx)
            content = getattr(meta, "content", None)
            if not parts and content is not None:
                parts = getattr(content, "parts", None)
            role = aggregated_roles.get(idx)
            if not role and content is not None:
                role = getattr(content, "role", None)
            combined_content = (
                genai.types.Content(role=role, parts=parts)
                if parts or role
                else content
            )
            combined_candidates.append(
                genai.types.Candidate(
                    content=combined_content,
                    citation_metadata=getattr(meta, "citation_metadata", None),
                    finish_message=getattr(meta, "finish_message", None),
                    token_count=getattr(meta, "token_count", None),
                    finish_reason=getattr(meta, "finish_reason", None),
                    avg_logprobs=getattr(meta, "avg_logprobs", None),
                    grounding_metadata=getattr(meta, "grounding_metadata", None),
                    index=getattr(meta, "index", idx),
                    logprobs_result=getattr(meta, "logprobs_result", None),
                    safety_ratings=getattr(meta, "safety_ratings", None),
                    url_context_metadata=getattr(meta, "url_context_metadata", None),
                )
            )

        if not combined_candidates:
            return final_chunk

        aggregated_response = genai.types.GenerateContentResponse(
            candidates=combined_candidates,
            usage_metadata=usage_metadata,
            prompt_feedback=prompt_feedback,
            response_id=response_id,
            model_version=model_version,
            automatic_function_calling_history=afc_history,
        )
        return aggregated_response

    def _handle_stream_chunk(
        self,
        chunk: genai.types.GenerateContentResponse,
        *,
        model_name: str,
    ) -> None:
        """Render incremental thoughts and outputs as they stream in."""

        console = self.status_console
        if console is None:
            return

        candidates = getattr(chunk, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                text = getattr(part, "text", "")
                if not text:
                    continue
                label = "thought" if self._is_thought_part(part) else "output"
                self._emit_stream_text(label=label, text=text, model_name=model_name)

    def _emit_stream_text(self, *, label: str, text: str, model_name: str) -> None:
        """Print incremental text for the provided label without flicker."""

        console = self.status_console
        if console is None or not text:
            return

        if label == "thought":
            self._start_thinking_indicator()
            self._emit_thought_markdown(text=text, model_name=model_name)
            return

        styles = {
            "output": "white",
        }
        style = styles.get(label, "white")

        key = f"{model_name}:{label}"
        if not self._stream_started.get(key):
            console.print("")
            console.print(
                f"{model_name} {label.upper()} → ", style=style, end=""
            )
            self._stream_started[key] = True

        console.print(text, style=style, end="")

    def _finalize_stream_display(self) -> None:
        """Ensure streaming lines end cleanly before other output."""

        if not self._stream_started:
            return
        console = self.status_console
        if console is None:
            return
        for key, started in list(self._stream_started.items()):
            if started:
                console.print("")
        self._stream_started.clear()
        self._stop_thinking_indicator()

    def _is_thought_part(self, part: Any) -> bool:
        marker = getattr(part, "thought", None)
        if isinstance(marker, bool):
            return marker
        if marker not in (None, False):
            return True

        role = getattr(part, "role", None)
        if isinstance(role, str) and role.lower() == "thought":
            return True

        kind = getattr(part, "kind_", None)
        if isinstance(kind, str) and "thought" in kind.lower():
            return True

        return False

    def _emit_thought_markdown(self, *, text: str, model_name: str) -> None:
        console = self.status_console
        if console is None:
            return
        snippet = (text or "").strip()
        if not snippet:
            self._stop_thinking_indicator()
            return

        console.print(
            Panel(
                Markdown(snippet),
                title=f"✦ thought → {model_name}",
                #subtitle=model_name,
                border_style="yellow",
                padding=(1, 2),
            )
        )
        self._stop_thinking_indicator()

    def _start_thinking_indicator(self) -> None:
        if self.status_console is None or self._thinking_status is not None:
            return
        status = self.status_console.status(
            "thinking",
            spinner="dots12",
            spinner_style="yellow",
        )
        status.start()
        self._thinking_status = status
        self._thinking_started_at = time.monotonic()

    def _stop_thinking_indicator(self) -> None:
        if self._thinking_status is None:
            return
        min_duration = 0.2
        if self._thinking_started_at is not None:
            elapsed = time.monotonic() - self._thinking_started_at
            if elapsed < min_duration:
                time.sleep(min_duration - elapsed)
        self._thinking_status.stop()
        self._thinking_status = None
        self._thinking_started_at = None
