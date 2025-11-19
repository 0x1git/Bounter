"""Core Gemini agent orchestration."""
from __future__ import annotations

from typing import Optional

from google import genai

from .config import BounterConfig
from .reporting import ScanReport
from .tools import build_system_command_tool


class BounterAgent:
    """Coordinates Gemini interactions, tools, and reporting."""

    def __init__(
        self,
        config: BounterConfig,
        report: ScanReport,
        client: Optional[genai.Client] = None,
        verbose: bool = False,
    ) -> None:
        self.config = config
        self.report = report
        self.client = client or genai.Client()
        self.verbose = verbose

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

        # Try models in configured order when rate limits are encountered.
        tried_models = []
        last_exception: Optional[Exception] = None

        for model_name in self.config.models_order:
            tried_models.append(model_name)
            prompt = self.build_prompt(target, description)

            # If this isn't the first model attempt, include context so the
            # next model knows what has been tested so far.
            if len(tried_models) > 1:
                context_lines = []
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

                if context_lines:
                    prompt = prompt + "\n\nCONTEXT:\n" + "\n".join(context_lines)

            tool_fn = build_system_command_tool(
                report=self.report, timeout=self.config.command_timeout
            )
            content_config = self.config.build_content_config([tool_fn])

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
                    # end_time tracks the last response time, but the scan is still ongoing
                    self.report.end_time = None
                    continue

                return response
            except genai.errors.ClientError as exc:  # pragma: no cover - depends on API
                last_exception = exc
                code = getattr(exc, "code", None)
                msg = (getattr(exc, "message", None) or str(exc) or "").lower()
                self._log(
                    f"Model '{model_name}' client error (code={code}, status={getattr(exc, 'status', None)}): {msg}"
                )

                if code == 429 or any(keyword in msg for keyword in ["rate", "quota", "too many requests"]):
                    self._log(
                        f"Rate limit detected for model '{model_name}' (code={code}); attempting next model"
                    )
                    continue

                raise
            except Exception as exc:  # pragma: no cover - runtime error handling
                last_exception = exc
                msg = str(exc).lower()
                self._log(f"Model '{model_name}' failed: {msg}")

                # Detect common rate-limit indicators and try the next model.
                if "rate" in msg or "429" in msg or "quota" in msg or "too many requests" in msg:
                    self._log(
                        f"Rate limit detected for model '{model_name}', trying next model"
                    )
                    continue
                # Non-rate-limit error -> re-raise so caller sees the issue.
                raise

        # If we reach here, all models failed due to rate limits (or other
        # transient errors). Surface the last exception if available.
        if last_exception:
            raise last_exception
        raise RuntimeError("No models available to process the request")
