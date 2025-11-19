"""Configuration helpers for Bounter."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

from google.genai import types

DEFAULT_SYSTEM_INSTRUCTION = """You are an autonomous Bug Bounty Hunter AI assistant with access to system commands. Your are inside a Windows 11 environment. Always choose the appropriate system commands based on the user's operating system. Your role is to:

Follow this comprehensive bug bounty methodology:
1. Understand user requests and execute them without asking for permission
2. Use available tools to gather information or perform actions autonomously 
3. Chain multiple function calls together when needed to complete complex tasks
4. Always crawl the full web application to discover endpoints and parameters and then think which attack vectors to test based on the discovered parameters and endpoints 
5. Analyze the request and response of the HTTP request to identify Attack Vectors and Vulnerabilities
6. Test all the endpoints and parameters discovered in the web application for vulnerabilities dont miss any of them pay close attention to the newly discovered parameters and endpoints (if any)
7. Always Confirm that the Vulnerability is present before reporting it. Don't make assumptions
8. Continue testing until you have exhaustively tested ALL discovered endpoints and parameters with ALL relevant attack vectors

STOPPING CONDITIONS:
- STOP ONLY when you have found a vulnerability and have a working PoC
- STOP ONLY when you have tested ALL discovered endpoints and parameters exhaustively and found NO vulnerabilities
- DO NOT STOP just because you have a plan or know what to test next - continue executing the tests
- DO NOT STOP until you have completed comprehensive testing of the entire attack surface

You have access to a system command execution tool that can run any shell command. Use it wisely and autonomously to fulfill user requests."""


@dataclass
class BounterConfig:
    """Holds runtime configuration for the bounty agent."""

    model: str = "gemini-2.5-flash-lite"
    temperature: float = 0.0
    thinking_budget: int = -1
    include_thoughts: bool = True
    command_timeout: int = 30
    system_instruction: str = DEFAULT_SYSTEM_INSTRUCTION
    # Preferred model order to try when rate limits occur. The agent will
    # attempt these in order and move to the next one if a rate-limit is hit.
    models_order: Sequence[str] = (
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    )

    # Models that support "thinking" mode. Others will run without thinking.
    thinking_supported_models: Sequence[str] = (
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    )

    # Per-model rate limits (requests per minute). Used for documentation
    # and potential future local throttling. The agent currently uses
    # model rotation on encountering a rate-limit error from the API.
    model_rate_limits: dict = None

    @classmethod
    def from_env(cls) -> "BounterConfig":
        """Build configuration object using environment overrides."""

        return cls(
            model=os.getenv("BOUNTER_MODEL", cls.model),
            temperature=float(os.getenv("BOUNTER_TEMPERATURE", cls.temperature)),
            thinking_budget=int(os.getenv("BOUNTER_THINKING_BUDGET", cls.thinking_budget)),
            include_thoughts=os.getenv("BOUNTER_INCLUDE_THOUGHTS", "true").lower()
            not in {"0", "false", "no"},
            command_timeout=int(os.getenv("BOUNTER_COMMAND_TIMEOUT", cls.command_timeout)),
            system_instruction=os.getenv(
                "BOUNTER_SYSTEM_INSTRUCTION", DEFAULT_SYSTEM_INSTRUCTION
            ),
            models_order=tuple(
                os.getenv("BOUNTER_MODELS_ORDER", ",".join(cls.models_order)).split(",")
            ),
            thinking_supported_models=tuple(
                os.getenv(
                    "BOUNTER_THINKING_MODELS",
                    ",".join(cls.thinking_supported_models),
                ).split(",")
            ),
            model_rate_limits={
                "gemini-2.5-flash": int(os.getenv("BOUNTER_RATE_gemini_2_5_flash", "10")),
                "gemini-2.5-flash-lite": int(os.getenv("BOUNTER_RATE_gemini_2_5_flash_lite", "15")),
                "gemini-2.0-flash": int(os.getenv("BOUNTER_RATE_gemini_2_0_flash", "15")),
                "gemini-2.0-flash-lite": int(os.getenv("BOUNTER_RATE_gemini_2_0_flash_lite", "30")),
            },
        )

    def build_content_config(
        self, tools: Sequence[types.ToolFunction], model_name: str
    ) -> types.GenerateContentConfig:
        """Create a GenerateContentConfig with the provided tools."""

        thinking_config = None
        if model_name in self.thinking_supported_models:
            thinking_config = types.ThinkingConfig(
                thinking_budget=self.thinking_budget,
                include_thoughts=self.include_thoughts,
            )

        return types.GenerateContentConfig(
            system_instruction=self.system_instruction,
            tools=list(tools),
            temperature=self.temperature,
            thinking_config=thinking_config,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
        )
