"""Core Gemini agent orchestration."""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from google import genai

from .config import BounterConfig
from .reporting import ScanReport
from .tools import build_system_command_tool


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
    ) -> None:
        self.config = config
        self.report = report
        self.client = client or genai.Client()
        self.verbose = verbose
        self.on_tool_event = on_tool_event
        self._model_usage: dict[str, dict[str, float]] = {}
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

    def _acquire_model_slot(self, model_name: str) -> bool:
        """Reserve a slot for the model if it has remaining buffered capacity."""

        limits = self.config.model_rate_limits or {}
        limit = limits.get(model_name)
        if not limit or limit <= 0:
            return True

        now = time.monotonic()
        usage = self._model_usage.get(model_name)
        if not usage or now - usage["window_start"] >= self.RATE_LIMIT_WINDOW:
            usage = {"window_start": now, "count": 0}
            self._model_usage[model_name] = usage

        threshold = max(limit - self.RATE_LIMIT_BUFFER, 1)
        if usage["count"] >= threshold:
            self._record_rate_limit_note(model_name, int(usage["count"]), limit)
            return False

        usage["count"] += 1
        return True

    def _record_rate_limit_note(self, model_name: str, count: int, limit: int) -> None:
        limit_desc = f"{limit} RPM" if limit else "its rate limit"
        note = (
            f"Model '{model_name}' processed {count} requests in the current minute. "
            f"Switching away early to avoid hitting {limit_desc}."
        )
        if note not in self._rate_limit_notes:
            self._rate_limit_notes.append(note)

    def _time_until_slot_available(self) -> float | None:
        limits = self.config.model_rate_limits or {}
        soonest: float | None = None
        now = time.monotonic()
        for model_name, usage in self._model_usage.items():
            if model_name not in limits:
                continue
            elapsed = now - usage["window_start"]
            remaining = self.RATE_LIMIT_WINDOW - elapsed
            if remaining > 0:
                if soonest is None or remaining < soonest:
                    soonest = remaining
        return soonest

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

        # Try models in configured order, rotating proactively before limits hit.
        tried_models: list[str] = []
        last_exception: Optional[Exception] = None
        self._rate_limit_notes = []

        while True:
            all_throttled = True
            for model_name in self.config.models_order:
                if not self._acquire_model_slot(model_name):
                    self._log(
                        f"Model '{model_name}' is approaching its RPM limit; rotating to the next model"
                    )
                    continue

                all_throttled = False
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
                            context_lines.append(
                                f"- {cmd.command} (success={cmd.success})"
                            )
                    if self.report.thinking_summary:
                        context_lines.append("Thinking summary so far:")
                        context_lines.extend(
                            f"- {t}" for t in self.report.thinking_summary
                        )
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
                )
                content_config = self.config.build_content_config(
                    [tool_fn], model_name=model_name
                )

                self._log(
                    f"Dispatching prompt to model '{model_name}' (attempt {len(tried_models)})"
                )

                try:
                    response = self.client.models.generate_content(
                        model=model_name, contents=prompt, config=content_config
                    )
                    self._log(f"Model '{model_name}' responded successfully")
                    self.report.update_from_response(response)

                    if self._response_indicates_rate_limit(response):
                        self._log(
                            f"Model '{model_name}' reported a rate limit in its response; switching models"
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
                        self._record_rate_limit_note(
                            model_name,
                            0,
                            self.config.model_rate_limits.get(model_name, 0)
                            if self.config.model_rate_limits
                            else 0,
                        )
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
                        continue
                    raise

            if all_throttled:
                wait_time = self._time_until_slot_available()
                if wait_time:
                    sleep_for = min(wait_time, 5.0)
                    self._log(
                        f"All configured models are near their RPM buffer. Sleeping {sleep_for:.1f}s before retrying."
                    )
                    time.sleep(sleep_for)
                    continue
                break

            if last_exception:
                raise last_exception
            break

        if last_exception:
            raise last_exception
        raise RuntimeError("No models available to process the request")
