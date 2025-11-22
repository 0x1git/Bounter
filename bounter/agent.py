"""Core Gemini agent orchestration."""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Callable, Optional

from google import genai
from rich.console import Console
from rich.progress import Progress

from .config import BounterConfig
from .reporting import ScanReport
from .tools import build_system_command_tool
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

    RATE_LIMIT_KEYWORDS = (
        "rate limit",
        "rate-limit",
        "quota",
        "too many requests",
        "429",
        "limit reached",
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
            prompt = self.build_prompt(target, description)

            if len(tried_models) > 1 or self._rate_limit_notes:
                context_lines = []
                if len(tried_models) > 1:
                    context_lines.append(
                        "Previously attempted models: " + ", ".join(tried_models[:-1])
                    )
                if self.report.commands:
                    context_lines.append("Commands executed so far:")
                    for cmd in self.report.commands:
                        context_lines.append(f"- {cmd.command} (success={cmd.success})")
                if self.report.thinking_summary:
                    context_lines.append("Thinking summary so far:")
                    context_lines.extend(f"- {t}" for t in self.report.thinking_summary)
                if self.report.final_analysis:
                    context_lines.append("Final analysis so far:")
                    context_lines.append(self.report.final_analysis)
                if self._rate_limit_notes:
                    context_lines.append("Rate limit observations:")
                    context_lines.extend(f"- {note}" for note in self._rate_limit_notes)

                if context_lines:
                    prompt = prompt + "\n\nCONTEXT:\n" + "\n".join(context_lines)

            tool_fn = build_system_command_tool(
                report=self.report,
                timeout=self.config.command_timeout,
                verbose=self.verbose,
                on_command=self.on_tool_event,
                status_console=self.status_console,
                progress=self.progress,
            )
            content_config = self.config.build_content_config([tool_fn], model_name=model_name)

            self._log(
                f"Dispatching prompt to model '{model_name}' (attempt {len(tried_models)})"
            )

            spinner_message = f"[cyan]Calling {model_name}"
            status_cm = (
                self.status_console.status(spinner_message, spinner="dots8")
                if self.status_console
                else nullcontext()
            )

            progress_cm = track_progress(
                self.progress, f"LLM → {model_name}"
            )

            try:
                with progress_cm, status_cm:
                    response = self.client.models.generate_content(
                        model=model_name, contents=prompt, config=content_config
                    )
                self._log(f"Model '{model_name}' responded successfully")
                self.report.update_from_response(response)

                if self._response_indicates_rate_limit(response):
                    self._log(
                        f"Model '{model_name}' reported a rate limit in its response; switching models"
                    )
                    self._record_rate_limit_note(
                        model_name, "Model reported rate limit signal in response"
                    )
                    self.report.end_time = None
                    continue

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
                    continue

                raise
            except Exception as exc:  # pragma: no cover - runtime error handling
                last_exception = exc
                msg = str(exc).lower()
                self._log(f"Model '{model_name}' failed: {msg}")

                if self._is_rate_limit_error(None, msg):
                    self._log(
                        f"Rate limit detected for model '{model_name}', trying next model"
                    )
                    self._record_rate_limit_note(model_name, str(exc))
                    continue
                raise

        if last_exception:
            raise last_exception
        raise RuntimeError("No models available to process the request")
